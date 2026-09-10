import datetime
import logging
import os
from enum import Enum
from zoneinfo import ZoneInfo

from hyundai_kia_connect_api import Vehicle, VehicleManager
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    RateLimitingError,
    RequestTimeoutError,
)

from SpritMonitorClient import SpritMonitorClient, SpritMonitorError

# Optional exceptions: only present in newer versions of the library.
try:
    from hyundai_kia_connect_api.exceptions import AuthenticationOTPRequired
except ImportError:  # pragma: no cover - depends on library version
    AuthenticationOTPRequired = None

try:
    from hyundai_kia_connect_api.exceptions import ConsentRequiredError
except ImportError:  # pragma: no cover - depends on library version
    ConsentRequiredError = None


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class ChargeType(Enum):
    DC = "DC"
    AC = "AC"
    UNKNOWN = "UNKNOWN"


class VehicleClient:
    """
    Vehicle client class
    Role:
    - get trip data from UVO/Bluelink API
    - handle additional (calculated) attributes that the API does not provide
    - send consumption data to Spritmonitor API
    """

    def __init__(
        self,
        username: str,
        password: str,
        pin: str,
        vehicle_uuid: str,
        kia_language: str = "hu",
    ):
        """
        Initialize the VehicleClient
        :param username: UVO/Bluelink username
        :param password: UVO/Bluelink password
        :param pin: UVO/Bluelink PIN
        :param vehicle_uuid: UVO/Bluelink vehicle UUID
        :param kia_language: Language for the UVO/Bluelink API (default: "hu")
        """
        self.charging_power_in_kilowatts: float = 0.0  # default = 0 (not charging)
        self.charge_type: ChargeType = ChargeType.UNKNOWN
        self.vehicle: Vehicle | None = None
        self.vm: VehicleManager | None = None
        self.logger = logging.getLogger(__name__)
        self.vehicle_uuid = vehicle_uuid
        self.kia_language = kia_language

        # Timezone the UVO data is interpreted in. The library reports EU data in
        # Europe/Berlin; running in a UTC container would otherwise shift day
        # boundaries around midnight.
        self.timezone = ZoneInfo(os.getenv("DATA_TIMEZONE", "Europe/Budapest"))

        # Initialize SpritMonitor client
        self.spritmonitor_vehicle_id = os.environ.get("SPRITMONITOR_VEHICLE_ID")
        self.spritmonitor_tank_id = os.environ.get("SPRITMONITOR_TANK_ID", "1")
        self.spritmonitor = SpritMonitorClient(
            bearer_token=os.environ.get("SPRITMONITOR_BEARER_TOKEN"),
            app_token=os.environ.get("SPRITMONITOR_APP_TOKEN"),
        )

        # Get electricity price from environment variable (optional)
        electricity_price_str = os.getenv("ELECTRICITY_PRICE")
        self.electricity_price = float(electricity_price_str) if electricity_price_str else None

        # Get currency ID from environment variable, default to 11 (HUF)
        self.currency_id = int(os.getenv("CURRENCY_ID", "11"))

        # Get country from environment variable, default to HU
        self.country = os.getenv("COUNTRY", "HU")

        # Get station name from environment variable, default to home
        self.station_name = os.getenv("STATION_NAME", "home")

        # Spritmonitor fuel sort: 19 = Elektrizitaet, 24 = Oekostrom
        self.fuelsort_id = os.getenv("SM_FUELSORT_ID", "19")

        # net = what left the battery for good, gross = including regenerated energy
        self.quantity_mode = os.getenv("SM_QUANTITY_MODE", "gross").strip().lower()

        # Spritmonitor percent handling mode:
        # When True (default), we always send 100% state-of-charge so that Spritmonitor
        # does not apply its partial-refueling heuristics, which would distort
        # consumption for our daily aggregated EV data.
        self.spritmonitor_force_full_percent = _env_flag("SM_FORCE_FULL_PERCENT", True)

        # The charge type and charging power we can read from the API describe the
        # CURRENT state of the car, not the state during a historical day. Attaching
        # them to backfilled entries produces misleading data, so it is opt-in.
        self.send_live_charge_info = _env_flag("SM_SEND_LIVE_CHARGE_INFO", False)

        # Each backfilled day costs at least one UVO API call (update_day_trip_info)
        # and we are limited to 200 requests a day. Cap how much we do per run.
        self.max_days_per_run = int(os.getenv("SM_MAX_DAYS_PER_RUN", "10"))

        # Battery/charging profile. Defaults match a Kia e-Niro MY20:
        # 64 kWh usable + unusable kWh + charger losses.
        self.battery_total_kwh = float(os.getenv("BATTERY_TOTAL_KWH", "70"))

        self._init_vehicle_manager(username, password, pin)

    def _init_vehicle_manager(self, username, password, pin):
        self.vm = VehicleManager(
            region=1,
            brand=1,
            username=username,
            password=password,
            pin=pin,
            language=self.kia_language,
        )
        self.logger.info("Initializing vehicle connection...")
        # check_and_refresh_token() logs in and fills the vehicle list on its own.
        self.vm.check_and_refresh_token()
        self.vehicle = self.vm.get_vehicle(self.vehicle_uuid)

    def initialize(self):
        """
        Make sure we have a valid token and a resolved vehicle.

        Deliberately cheap: no state fetch happens here, refresh() does that in one
        pass so we do not spend the daily API budget twice.

        :raises: Exception if token refresh or vehicle lookup fails
        """
        self.vm.check_and_refresh_token()
        self.vehicle = self.vm.get_vehicle(self.vehicle_uuid)
        self.logger.info("Vehicle initialization completed successfully")

    def get_estimated_charging_power(self) -> float:
        """
        Roughly estimates charging speed based on:
        - charge limits for both AC and DC charging
        - current battery percentage (SoC) as reported by the car
        - charging time remaining as reported by the car

        Also updates self.charge_type and self.charging_power_in_kilowatts.

        :return: The estimated charging power in kilowatts
        """
        if not self.vehicle.ev_battery_is_charging:
            self.charge_type = ChargeType.UNKNOWN
            self.charging_power_in_kilowatts = 0.0
            return 0.0

        charge_duration = self.vehicle.ev_estimated_current_charge_duration
        if not charge_duration or charge_duration <= 0:
            self.logger.warning(
                "Car is charging but reported no estimated charge duration; "
                "cannot estimate charging power"
            )
            self.charge_type = ChargeType.UNKNOWN
            self.charging_power_in_kilowatts = 0.0
            return 0.0

        estimated_total_kwh_needed = self.battery_total_kwh

        percent_remaining = 100 - self.vehicle.ev_battery_percentage
        kwh_remaining = estimated_total_kwh_needed * percent_remaining / 100

        self.logger.debug(f"Kilowatthours needed for full battery: {kwh_remaining} kWh")

        # todo: there is a bug here: kwh_remaining does not take charge limits into
        #  account. however the "estimated charge time" provided by the car does.
        #  so this formula returns too high values (ex: 20kw when AC charging at home).
        charging_power_in_kilowatts = kwh_remaining / (charge_duration / 60)

        # the delta calculation between ac limits and percentage is a temporary fix
        # for the todo above
        if (
            charging_power_in_kilowatts > 8
            and self.vehicle.ev_charge_limits_ac - self.vehicle.ev_battery_percentage > 15
        ):
            # the car's onboard AC charger cannot exceed 7kW, or 11kW with the optional
            # upgrade. if power > 11kW, then assume we are DC charging. recalculate
            # values to take DC charge limits into account
            self.charge_type = ChargeType.DC
            percent_remaining = (
                self.vehicle.ev_charge_limits_dc - self.vehicle.ev_battery_percentage
            )
            kwh_remaining = estimated_total_kwh_needed * percent_remaining / 100
            charging_power_in_kilowatts = kwh_remaining / (charge_duration / 60)

            # simulate DC charging power curve for 64kWh e-niro
            # source: https://support.fastned.nl/hc/fr/articles/4408899202193-Kia
            soc = self.vehicle.ev_battery_percentage
            if soc > 95:
                charging_power_in_kilowatts = min(5, charging_power_in_kilowatts)
            elif soc > 90:
                charging_power_in_kilowatts = min(10, charging_power_in_kilowatts)
            elif soc > 80:
                charging_power_in_kilowatts = min(20, charging_power_in_kilowatts)
            elif soc > 75:
                charging_power_in_kilowatts = min(35, charging_power_in_kilowatts)
            elif soc > 55:
                charging_power_in_kilowatts = min(55, charging_power_in_kilowatts)
            elif soc > 40:
                charging_power_in_kilowatts = min(70, charging_power_in_kilowatts)
            elif soc > 27:
                charging_power_in_kilowatts = min(77, charging_power_in_kilowatts)
        else:
            self.charge_type = ChargeType.AC

        self.charging_power_in_kilowatts = round(charging_power_in_kilowatts, 1)
        self.logger.info(
            f"Estimated charging power: {self.charging_power_in_kilowatts} kW "
            f"({self.charge_type.value})"
        )
        return self.charging_power_in_kilowatts

    def refresh(self) -> bool:
        """
        Force refresh vehicle status and process data.

        One pass only: force refresh -> read state -> read driving info -> upload.
        Every call here counts against the 200 requests/day UVO limit, cached ones
        included, so nothing is fetched twice.

        :return: True if the daily stats were processed, False if we bailed out early
        """
        self.logger.info("Refreshing token...")
        try:
            self.vm.check_and_refresh_token()
        except Exception as e:
            self.handle_api_exception(e)
            return False

        self.vehicle = self.vm.get_vehicle(self.vehicle_uuid)

        # A force refresh wakes the car up. If it does not answer we carry on with
        # whatever the server has cached - the historical daily stats we upload do
        # not depend on the live state anyway.
        self.logger.info("Performing force refresh...")
        try:
            self.vm.force_refresh_vehicle_state(self.vehicle.id)
        except Exception as e:
            self.logger.warning("Force refresh failed, falling back to the cached vehicle state")
            self.handle_api_exception(e)

        self.logger.info("Retrieving vehicle state from server...")
        try:
            response = self.vm.api._get_cached_vehicle_state(self.vm.token, self.vehicle)
        except Exception as e:
            self.handle_api_exception(e)
            return False

        self.vm.api._update_vehicle_properties(self.vehicle, response)
        self.get_estimated_charging_power()
        self._log_update_delta()

        # Get driving info to update daily stats
        try:
            response = self.vm.api._get_driving_info(self.vm.token, self.vehicle)
        except Exception as e:
            self.handle_api_exception(e)
            return False

        self.vm.api._update_vehicle_drive_info(self.vehicle, response)

        self.process_and_upload_daily_stats()
        return True

    def _log_update_delta(self):
        """Log how stale the data the server gave us is."""
        last_updated = getattr(self.vehicle, "last_updated_at", None)
        if not last_updated:
            return

        delta = datetime.datetime.now() - last_updated.replace(tzinfo=None)
        self.logger.info(
            f"Delta between last saved update and current time: "
            f"{int(delta.total_seconds())} seconds"
        )
        if delta.total_seconds() < 0:
            self.logger.warning(
                f"Negative delta ({delta.total_seconds()}s), probably a timezone issue."
            )

    def _today(self) -> datetime.date:
        """Today's date in the car's timezone, not the host's."""
        return datetime.datetime.now(self.timezone).date()

    def _get_latest_spritmonitor_entry(self):
        """
        Read the most recent Spritmonitor entry so we know where to continue.

        :return: (date, odometer) tuple, both None if there is nothing to continue from
        """
        try:
            latest_entries = self.spritmonitor.get_latest_fuelings(
                vehicle_id=self.spritmonitor_vehicle_id,
                tank_id=self.spritmonitor_tank_id,
                limit=2,  # Get 2 entries to have context if needed
            )
        except Exception as e:
            self.logger.error(f"Failed to get latest entry from Spritmonitor: {e}")
            return None, None

        if not latest_entries:
            self.logger.info("No existing entries in Spritmonitor")
            return None, None

        latest_entry = latest_entries[0]
        latest_date = datetime.datetime.strptime(latest_entry["date"], "%d.%m.%Y").date()
        latest_odometer = float(latest_entry["odometer"])
        self.logger.info(
            f"Latest Spritmonitor entry: date={latest_date}, odometer={latest_odometer}"
        )
        return latest_date, latest_odometer

    def process_and_upload_daily_stats(self):
        """
        Calculate odometer values for daily stats and upload to Spritmonitor.
        First checks the latest entry in Spritmonitor, then processes only older
        entries. Skips today's data to avoid frequent updates, and only uploads
        historical data that is complete.
        """
        if not self.spritmonitor_vehicle_id:
            self.logger.warning("Spritmonitor vehicle ID not set, skipping consumption data upload")
            return

        latest_date, _ = self._get_latest_spritmonitor_entry()

        daily_stats = getattr(self.vehicle, "daily_stats", None) or []
        if not daily_stats:
            self.logger.info("No daily stats to process")
            return

        # Sort daily stats from newest to oldest for odometer calculation
        sorted_daily_stats = sorted(daily_stats, key=lambda x: x.date, reverse=True)

        # Walk backwards from the current odometer, subtracting each day's distance.
        # odometers[day.date] is the reading at the END of that day.
        odometers = {}
        current_odometer = self.vehicle.odometer
        for day in sorted_daily_stats:
            odometers[day.date] = current_odometer
            current_odometer -= day.distance

        # Only complete days: skip today, and anything Spritmonitor already knows about
        today = self._today()
        filtered_stats = [
            day
            for day in sorted_daily_stats
            if (not latest_date or day.date.date() > latest_date) and day.date.date() < today
        ]

        if not filtered_stats:
            self.logger.info("No historical entries to upload")
            return

        filtered_stats.sort(key=lambda x: x.date)  # Sort oldest to newest

        if len(filtered_stats) > self.max_days_per_run:
            self.logger.info(
                f"Found {len(filtered_stats)} historical entries, processing the "
                f"oldest {self.max_days_per_run} this run (UVO API budget); "
                f"the rest follows on the next run"
            )
            filtered_stats = filtered_stats[: self.max_days_per_run]
        else:
            self.logger.info(f"Found {len(filtered_stats)} historical entries to upload")

        current_month = None
        for day in filtered_stats:
            # Check if we entered a new month
            month = day.date.strftime("%Y%m")
            if month != current_month:
                if not self.update_trip_info_for_month(month):
                    self.logger.warning(
                        f"Month trip info for {month} unavailable, trip details may be missing"
                    )
                current_month = month

            try:
                self.vm.update_day_trip_info(self.vehicle.id, day.date.strftime("%Y%m%d"))
            except Exception as e:
                self.handle_api_exception(e)
                # Rate limiting means every further call is wasted - stop the run.
                if isinstance(e, RateLimitingError):
                    return
                self.logger.warning(
                    f"Could not fetch trip info for {day.date.date()}, "
                    f"uploading without trip details"
                )

            # One bad day must not abort the remaining backfill.
            try:
                self.send_consumption_to_spritmonitor(day, odometers[day.date])
            except Exception as e:
                self.logger.error(
                    f"Skipping {day.date.date()}: failed to upload to Spritmonitor: {e}"
                )

    def update_trip_info_for_month(self, month_str: str) -> bool:
        """
        Update trip info for a specific month
        :param month_str: Month in YYYYMM format
        :return: True if update was successful, False otherwise
        """
        try:
            self.vm.update_month_trip_info(self.vehicle.id, month_str)
            return True
        except Exception as e:
            self.logger.error(f"Failed to get trip info for {month_str}: {e}")
            return False

    def handle_api_exception(self, exc: Exception):
        """
        In case of API error, this function defines what to do:
        - log error with a message that says what to do about it
        :param exc: the Exception returned by the library
        """
        # rate limiting: we are blocked for 24 hours
        if isinstance(exc, RateLimitingError):
            self.logger.exception(
                "we got rate limited, probably exceeded 200 requests. exiting",
                exc_info=exc,
            )

        # request timeout: vehicle could not be reached.
        # to prevent too many unsuccessful requests in a row (which would lead to rate
        # limiting) we stop here instead of retrying.
        elif isinstance(exc, RequestTimeoutError):
            self.logger.exception(
                "The vehicle did not respond. Exiting to prevent too many unsuccessful "
                "requests that would lead to rate limiting ",
                exc_info=exc,
            )

        # OTP / consent: needs a human, retrying only makes it worse
        elif AuthenticationOTPRequired is not None and isinstance(exc, AuthenticationOTPRequired):
            self.logger.error(
                "The Kia/Hyundai account requires a one-time password to log in. "
                "Complete the OTP flow in the official app, then run again. (%s)",
                exc,
            )

        elif ConsentRequiredError is not None and isinstance(exc, ConsentRequiredError):
            self.logger.error(
                "The Kia/Hyundai account is missing a required consent. "
                "Accept the new terms in the official app, then run again. (%s)",
                exc,
            )

        elif isinstance(exc, AuthenticationError):
            self.logger.exception("authentication failed:", exc_info=exc)

        # broad API error
        elif isinstance(exc, APIError):
            self.logger.exception("server responded with error:", exc_info=exc)

        # any other exception
        else:
            self.logger.exception("generic error:", exc_info=exc)

    @staticmethod
    def _build_note(day_stats, gross_kwh: float, net_kwh: float) -> str:
        """Build the human readable note attached to the Spritmonitor entry."""
        return (
            f"Engine: {round(day_stats.engine_consumption / 1000, 1)} kWh\n"
            f"Climate: {round(day_stats.climate_consumption / 1000, 1)} kWh\n"
            f"Electronics: "
            f"{round(day_stats.onboard_electronics_consumption / 1000, 1)} kWh\n"
            f"Battery Care: {round(day_stats.battery_care_consumption / 1000, 1)} kWh\n"
            f"Regenerated: {round(day_stats.regenerated_energy / 1000, 1)} kWh\n"
            f"Gross: {round(gross_kwh, 1)} kWh\n"
            f"Net: {round(net_kwh, 1)} kWh\n"
        )

    def _trip_summary(self):
        """
        Aggregate the individual trips of the currently loaded day.

        :return: (avg_speed, note_suffix) or (None, "") if there is nothing usable
        """
        day_trip_info = getattr(self.vehicle, "day_trip_info", None)
        trips = getattr(day_trip_info, "trip_list", None) if day_trip_info else None
        if not trips:
            return None, ""

        # Calculate daily statistics from individual trips
        total_drive_time = sum(trip.drive_time for trip in trips)  # in minutes
        total_idle_time = sum(trip.idle_time for trip in trips)  # in minutes

        # Filter out invalid trips (with 0 distance or speed)
        valid_trips = [t for t in trips if t.distance > 0 and t.max_speed > 0]
        if not valid_trips:
            return None, ""

        total_distance = sum(trip.distance for trip in valid_trips)
        avg_speed = sum(trip.avg_speed * trip.distance for trip in valid_trips) / total_distance
        max_speed = max(trip.max_speed for trip in valid_trips)

        # Convert minutes to hours and remaining minutes
        drive_time_hours = total_drive_time // 60
        drive_time_minutes = total_drive_time % 60

        note_suffix = (
            f"\nTrip details:"
            f"\n- Drive time: {drive_time_hours}h {drive_time_minutes}m"
            f"\n- Idle time: {total_idle_time}m"
            f"\n- Avg speed: {avg_speed:.1f} km/h"
            f"\n- Max speed: {max_speed} km/h"
            f"\n- Number of trips: {len(trips)}"
        )
        return round(avg_speed, 1), note_suffix

    def build_consumption_payload(self, day_stats, odometer) -> dict:
        """
        Convert a KIA UVO daily stats record into a Spritmonitor payload.

        Kept separate from the upload so it can be unit tested without network access.

        :param day_stats: Daily statistics from KIA UVO API
        :param odometer: Odometer reading at the end of that day, in km
        :return: payload dict for SpritMonitorClient.upload_consumption_data
        """
        # total_consumed includes the energy regeneration fed back into the battery,
        # so "net" is what actually left the battery for good.
        gross_kwh = max(day_stats.total_consumed / 1000.0, 0.0)
        net_kwh = max((day_stats.total_consumed - day_stats.regenerated_energy) / 1000.0, 0.0)
        qty_kwh = gross_kwh if self.quantity_mode == "gross" else net_kwh

        distance = day_stats.distance
        # Spritmonitor expects the board computer value in the vehicle's configured
        # unit, which for this project is kWh/100km (see README).
        bc_consumption = round(qty_kwh / distance * 100, 1) if distance > 0 else 0

        consumption_data = {
            "date": day_stats.date.strftime("%d.%m.%Y"),  # DD.MM.YYYY
            "odometer": int(odometer),
            "trip": round(distance, 1),  # distance in km
            "quantity": round(qty_kwh, 1),  # kWh
            "fuelsortid": self.fuelsort_id,
            "quantityunitid": 5,  # kWh
            "country": self.country,
            "stationname": self.station_name,
            # Force 100% by default so Spritmonitor does not apply its
            # partial-refueling heuristics
            "percent": 100
            if self.spritmonitor_force_full_percent
            else self.vehicle.ev_battery_percentage,
            # We aggregate a whole day, so mark it full for Spritmonitor to calculate
            # consumption correctly.
            "type": "full",
            "bc_consumption": bc_consumption,  # kWh/100km
            "bc_quantity": round(qty_kwh, 1),  # same basis as quantity
            "bc_speed": 0,  # Will be updated if we have valid trips
        }

        # charge_type/charging_power describe the car right now, not this historical
        # day, so they are only sent when explicitly asked for.
        if self.send_live_charge_info and self.charge_type is not ChargeType.UNKNOWN:
            consumption_data["charge_info"] = self.charge_type.value.lower()
            consumption_data["charging_power"] = self.charging_power_in_kilowatts

        # Add price related fields only if electricity_price is set
        if self.electricity_price is not None:
            consumption_data.update(
                {
                    "price": self.electricity_price,  # Price per kWh
                    "currencyid": self.currency_id,
                    "pricetype": 1,  # 1 = unit price (per kWh)
                }
            )

        consumption_data["note"] = self._build_note(day_stats, gross_kwh, net_kwh)

        avg_speed, note_suffix = self._trip_summary()
        if avg_speed is not None:
            consumption_data["bc_speed"] = avg_speed
            consumption_data["note"] += note_suffix

        return consumption_data

    def send_consumption_to_spritmonitor(self, day_stats, odometer=None):
        """
        Send consumption data to Spritmonitor API.

        :param day_stats: Daily statistics from KIA UVO API
        :param odometer: Odometer reading at the end of that day. Falls back to the
                         attribute on day_stats for backwards compatibility.
        """
        if not self.spritmonitor_vehicle_id:
            self.logger.warning("Spritmonitor vehicle ID not set, skipping consumption data upload")
            return

        if odometer is None:
            odometer = getattr(day_stats, "odometer", None)
        if odometer is None:
            raise ValueError(f"No odometer value available for {day_stats.date.date()}")

        consumption_data = self.build_consumption_payload(day_stats, odometer)

        try:
            self.spritmonitor.upload_consumption_data(
                vehicle_id=self.spritmonitor_vehicle_id,
                tank_id=self.spritmonitor_tank_id,
                data=consumption_data,
            )
        except SpritMonitorError as e:
            self.logger.error(f"Failed to upload consumption data to Spritmonitor: {e}")
            raise

        self.logger.info(
            f"Successfully uploaded consumption data to Spritmonitor for "
            f"{day_stats.date.strftime('%Y-%m-%d')}"
        )
