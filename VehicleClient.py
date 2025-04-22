import os
import logging
import datetime

from datetime import timedelta
from enum import Enum
from hyundai_kia_connect_api import Vehicle, VehicleManager
from hyundai_kia_connect_api.exceptions import RateLimitingError, APIError, RequestTimeoutError
from SpritMonitorClient import SpritMonitorClient

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

    def __init__(self, username: str, password: str, pin: str, vehicle_uuid: str):
        """
        Initialize the VehicleClient
        :param username: UVO/Bluelink username
        :param password: UVO/Bluelink password
        :param pin: UVO/Bluelink PIN
        :param vehicle_uuid: UVO/Bluelink vehicle UUID
        """
        self.interval_in_seconds: int = 3600 * 4  # default
        self.charging_power_in_kilowatts: int = 0  # default = 0 (not charging)
        self.charge_type: ChargeType = ChargeType.UNKNOWN
        self.vehicle: [Vehicle, None] = None
        self.vm = None
        self.logger = logging.getLogger(__name__)
        self.trips = None  # vehicle trips. better motel than the one in the library
        self.vehicle_uuid = vehicle_uuid

        # Initialize SpritMonitor client
        self.spritmonitor_vehicle_id = os.environ.get("SPRITMONITOR_VEHICLE_ID")
        self.spritmonitor_tank_id = os.environ.get("SPRITMONITOR_TANK_ID", "1")  # Default to 1
        self.spritmonitor = SpritMonitorClient(
            bearer_token=os.environ.get("SPRITMONITOR_BEARER_TOKEN"),
            app_token=os.environ.get("SPRITMONITOR_APP_TOKEN")
        )

        # Get electricity price from environment variable, default to 41
        self.electricity_price = float(os.getenv('ELECTRICITY_PRICE', 41))
        
        # Get currency ID from environment variable, default to 11 (HUF)
        self.currency_id = int(os.getenv('CURRENCY_ID', 11))

        # interval in seconds between checks for cached requests
        # we are limited to 200 requests a day, including cached
        # that's about one every 8 minutes
        # we set it to 4 hours for cached refreshes.
        self.CACHED_REFRESH_INTERVAL = 3600 * 4

        self.CAR_OFF_FORCE_REFRESH_INTERVAL = 3600 * 6

        self.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL = 600
        self.DC_CHARGE_FORCE_REFRESH_INTERVAL = 1800
        self.AC_CHARGE_FORCE_REFRESH_INTERVAL = 1800

        self.vm = VehicleManager(region=1, brand=1, username=username,
                                 password=password, pin=pin)

        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    def initialize(self):
        """
        Initialize the connection with the vehicle and refresh the token
        
        :raises: Exception if token refresh or vehicle initialization fails
        """
        self.logger.info("Initializing vehicle connection...")

        if len(self.vm.vehicles) == 0 and self.vm.token:
            # supposed bug in lib: if initialization fails due to rate limiting, vehicles list is never filled
            # reset token to login again, the lib will then fill the list correctly
            self.vm.token = None

        try:
            self.vm.check_and_refresh_token()
        except Exception as e:
            self.handle_api_exception(e)
            raise

        self.vehicle = self.vm.get_vehicle(self.vehicle_uuid)

        try:
            response = self.vm.api._get_cached_vehicle_state(self.vm.token, self.vehicle)
        except Exception as e:
            self.handle_api_exception(e)
            raise

        self.vm.api._update_vehicle_properties(self.vehicle, response)
        self.get_estimated_charging_power()

        # Force refresh vehicle state to get latest data
        try:
            self.vm.force_refresh_vehicle_state(self.vehicle.id)
            self.vm.update_vehicle_with_cached_state(self.vehicle.id)
        except Exception as e:
            self.handle_api_exception(e)
            raise

        self.logger.info("Vehicle initialization completed successfully")

    def get_estimated_charging_power(self) -> float:
        """
        Roughly estimates charging speed based on:
        - charge limits for both AC and DC charging
        - current battery percentage (SoC) as reported by the car
        - external temperature
        - charging time remaining as reported by the car
        :return: The estimated charging power in kilowatts
        """

        if not self.vehicle.ev_battery_is_charging:
            return 0

        estimated_niro_total_kwh_needed = 70  # 64 usable kwh + unusable kwh + charger losses

        percent_remaining = 100 - self.vehicle.ev_battery_percentage
        kwh_remaining = estimated_niro_total_kwh_needed * percent_remaining / 100

        print(f"Kilowatthours needed for full battery: {kwh_remaining} kWh")

        # todo: there is a bug here: kwh_remaining does not take charge limits into account.
        #  however the "estimated charge time" provided by the car does.
        #  so this formula returns too high values (ex: 20kw when AC charging at home).
        charging_power_in_kilowatts = kwh_remaining / (self.vehicle.ev_estimated_current_charge_duration / 60)

        # the delta calculation between ac limits and percentage is a temporary fix for the todo above
        if (charging_power_in_kilowatts > 8
                and self.vehicle.ev_charge_limits_ac - self.vehicle.ev_battery_percentage > 15):

            # the car's onboard AC charger cannot exceed 7kW, or 11kW with the optional upgrade
            # if power > 11kW, then assume we are DC charging. recalculate values to take DC charge limits into account
            self.charge_type = ChargeType.DC
            percent_remaining = self.vehicle.ev_charge_limits_dc - self.vehicle.ev_battery_percentage
            kwh_remaining = estimated_niro_total_kwh_needed * percent_remaining / 100
            self.charging_power_in_kilowatts = kwh_remaining / (self.vehicle.ev_estimated_current_charge_duration / 60)

            # simulate DC charging power curve for 64kWh e-niro
            # source: https://support.fastned.nl/hc/fr/articles/4408899202193-Kia

            if self.vehicle.ev_battery_percentage > 95:
                charging_power_in_kilowatts = min(5, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 90:
                charging_power_in_kilowatts = min(10, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 80:
                charging_power_in_kilowatts = min(20, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 75:
                charging_power_in_kilowatts = min(35, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 55:
                charging_power_in_kilowatts = min(55, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 40:
                charging_power_in_kilowatts = min(70, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 27:
                charging_power_in_kilowatts = min(77, charging_power_in_kilowatts)

        else:
            self.charge_type = ChargeType.AC

        print(f"Estimated charging power: {round(charging_power_in_kilowatts, 1)} kW")
        return round(charging_power_in_kilowatts, 1)

    def refresh(self):
        """
        Force refresh vehicle status and process data.
        """
        self.logger.info("refreshing token...")

        if len(self.vm.vehicles) == 0 and self.vm.token:
            # supposed bug in lib: if initialization fails due to rate limiting, vehicles list is never filled
            # reset token to login again, the lib will then fill the list correctly
            self.vm.token = None

        # this command does NOT refresh vehicles (at least for EU and if there is not a preexisting token)
        try:
            self.vm.check_and_refresh_token()
        except Exception as e:
            self.handle_api_exception(e)
            return

        self.vehicle = self.vm.get_vehicle(self.vehicle_uuid)
        # fetch cached status, but do not retrieve driving info (driving stats) just yet, to prevent making too
        # many API calls. yes, cached calls also increment the API limit counter.

        try:
            response = self.vm.api._get_cached_vehicle_state(self.vm.token, self.vehicle)
        except Exception as e:
            self.handle_api_exception(e)
            return

        self.vm.api._update_vehicle_properties(self.vehicle, response)

        self.get_estimated_charging_power()

        # Get driving info to update daily stats
        try:
            response = self.vm.api._get_driving_info(self.vm.token, self.vehicle)
        except Exception as e:
            self.handle_api_exception(e)
            return

        self.vm.api._update_vehicle_drive_info(self.vehicle, response)

        # Process and upload daily stats
        self.process_and_upload_daily_stats()

        delta = datetime.datetime.now() - self.vehicle.last_updated_at.replace(tzinfo=None)

        self.logger.info(f"Delta between last saved update and current time: {int(delta.total_seconds())} seconds")

        if delta.total_seconds() < 0:
            self.logger.error(
                f"Negative delta ({delta.total_seconds()}s), probably a timezone issue. Check your logic.")
            raise RuntimeError()

        self.logger.info("Performing force refresh...")
        try:
            self.vm.force_refresh_vehicle_state(self.vehicle.id)
        except Exception as e:
            self.handle_api_exception(e)
            return

        self.logger.info(f"Data received by server. Now retrieving from server...")

        try:
            self.vm.update_vehicle_with_cached_state(self.vehicle.id)
        except Exception as e:
            self.handle_api_exception(e)
            return

        self.get_estimated_charging_power()

        # Get latest driving info and upload to Spritmonitor
        try:
            response = self.vm.api._get_driving_info(self.vm.token, self.vehicle)
        except Exception as e:
            self.handle_api_exception(e)
            return

        self.vm.api._update_vehicle_drive_info(self.vehicle, response)

        # Process and upload daily stats
        self.process_and_upload_daily_stats()

    def process_and_upload_daily_stats(self):
        """
        Calculate odometer values for daily stats and upload to Spritmonitor.
        First checks the latest entry in Spritmonitor, then processes only older entries.
        Skips today's data to avoid frequent updates, and only uploads historical data
        that is complete.
        """
        try:
            # Get the latest entry from Spritmonitor
            latest_entries = self.spritmonitor.get_latest_fuelings(
                vehicle_id=self.spritmonitor_vehicle_id,
                tank_id=self.spritmonitor_tank_id,
                limit=2  # Get 2 entries to have context if needed
            )
            
            latest_date = None
            latest_odometer = None
            
            if latest_entries:
                latest_entry = latest_entries[0]
                latest_date = datetime.datetime.strptime(latest_entry['date'], '%d.%m.%Y').date()
                latest_odometer = float(latest_entry['odometer'])
                self.logger.info(f"Latest Spritmonitor entry: date={latest_date}, odometer={latest_odometer}")
            else:
                self.logger.info("No existing entries in Spritmonitor")

        except Exception as e:
            self.logger.error(f"Failed to get latest entry from Spritmonitor: {e}")
            latest_date = None
            latest_odometer = None

        # Sort daily stats from newest to oldest for odometer calculation
        sorted_daily_stats = sorted(self.vehicle.daily_stats, key=lambda x: x.date, reverse=True)
        
        if not sorted_daily_stats:
            self.logger.info("No daily stats to process")
            return

        # Calculate odometer values for each day
        current_odometer = self.vehicle.odometer
        for day in sorted_daily_stats:
            day.odometer = current_odometer
            current_odometer -= day.distance

        # Filter out today's data and days that are already in Spritmonitor
        today = datetime.date.today()
        if latest_date and latest_date >= (today - timedelta(days=1)):
            self.logger.info("No historical entries to process - all data up to latest entry is already uploaded")
            return

        # Filter and sort stats
        filtered_stats = [
            day for day in sorted_daily_stats
            if (not latest_date or day.date.date() > latest_date) and day.date.date() < today
        ]

        if filtered_stats:
            self.logger.info(f"Found {len(filtered_stats)} historical entries to upload")
            filtered_stats.sort(key=lambda x: x.date)  # Sort oldest to newest
            
            current_month = None
            for day in filtered_stats:
                # Check if we entered a new month
                month = day.date.strftime("%Y%m")
                if month != current_month:
                    self.update_trip_info_for_month(month)
                    current_month = month
                
                try:
                    self.vm.update_day_trip_info(self.vehicle.id, day.date.strftime("%Y%m%d"))
                except Exception as e:
                    self.handle_api_exception(e)
                    return
                
                self.send_consumption_to_spritmonitor(day)
        else:
            self.logger.info("No historical entries to upload")

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
        - log error
        - sleep
        :param exc: the Exception returned by the library
        """

        # rate limiting: we are blocked for 24 hours
        if isinstance(exc, RateLimitingError):
            self.logger.exception(
                "we got rate limited, probably exceeded 200 requests. exiting",
                exc_info=exc)
            # time.sleep(3600 * 4)
            return

        # request timeout: vehicle could not be reached.
        # to prevent too many unsuccessful requests in a row (which would lead to rate limiting) we sleep for a while.
        elif isinstance(exc, RequestTimeoutError):
            self.logger.exception(
                "The vehicle did not respond. Exiting to prevent too many unsuccessful requests "
                "that would lead to rate limiting ",
                exc_info=exc)
            # time.sleep(3600)

        # broad API error
        elif isinstance(exc, APIError):
            self.logger.exception("server responded with error:", exc_info=exc)
            return

        # any other exception
        else:
            self.logger.exception("generic error:", exc_info=exc)
            return

    def send_consumption_to_spritmonitor(self, day_stats):
        """
        Send consumption data to Spritmonitor API.
        
        :param day_stats: Daily statistics from KIA UVO API
        """
        if not self.spritmonitor_vehicle_id:
            self.logger.warning("Spritmonitor vehicle ID not set, skipping consumption data upload")
            return

        try:
            # Get tank ID for EV charging if not already cached
            # if not hasattr(self, 'spritmonitor_tank_id'):
            #     tanks = self.spritmonitor.get_tanks(self.spritmonitor_vehicle_id)
            #     # Find the tank ID for electric charging
            #     for tank in tanks:
            #         if tank.get('fuelsorttype') == 5:  # electricity
            #             self.spritmonitor_tank_id = tank.get('id')
            #             break
            #     if not hasattr(self, 'spritmonitor_tank_id'):
            #         raise Exception("No electric charging tank found in Spritmonitor vehicle configuration")
            # Convert KIA UVO data format to Spritmonitor format
            consumption_data = {
                "date": day_stats.date.strftime("%d.%m.%Y"),  # Convert to DD.MM.YYYY format
                "odometer": int(day_stats.odometer),
                "trip": round(day_stats.distance, 1),  # distance in km
                "quantity": round(day_stats.total_consumed / 1000, 1),  # Convert Wh to kWh
                "fuelsortid": 5,  # 5 = Electricity
                "quantityunitid": 5,  # kWh
                "country": "HU",
                "stationname": "home",
                "charging_power": self.charging_power_in_kilowatts,
                "charging_duration": 0,  # We don't have this info from KIA UVO
                "charge_info": f"{self.charge_type.value.lower()},source_vehicle",
                "percent": self.vehicle.ev_battery_percentage,
                "type": "full",  # We don't have this info from KIA UVO, set to full so Spritmonitor calculates consumption correctly
                "bc_consumption": round(day_stats.distance / (day_stats.total_consumed / 1000), 1) if day_stats.total_consumed > 0 else 0,  # km/kWh
                "bc_quantity": round(day_stats.total_consumed / 1000, 1),  # Total consumption in kWh
                "bc_speed": 0,  # Will be updated if we have valid trips
                "price": self.electricity_price,  # Price per kWh
                "currencyid": self.currency_id,
                "pricetype": 1,  # 1 = unit price (per kWh)
            }
            consumption_data["note"] = (
                f"Engine: {round(day_stats.engine_consumption / 1000, 1)} kWh\n"
                f"Climate: {round(day_stats.climate_consumption / 1000, 1)} kWh\n"
                f"Electronics: {round(day_stats.onboard_electronics_consumption / 1000, 1)} kWh\n"
                f"Battery Care: {round(day_stats.battery_care_consumption / 1000, 1)} kWh\n"
                f"Regenerated: {round(day_stats.regenerated_energy / 1000, 1)} kWh\n"
                f"Net Consumption: {round((day_stats.total_consumed - day_stats.regenerated_energy) / 1000, 1)} kWh\n"
            )
            
            if hasattr(self.vehicle, 'day_trip_info') and self.vehicle.day_trip_info and hasattr(self.vehicle.day_trip_info, 'trip_list'):
                trips = self.vehicle.day_trip_info.trip_list
                if trips:
                    # Calculate daily statistics from individual trips
                    total_drive_time = sum(trip.drive_time for trip in trips)  # in minutes
                    total_idle_time = sum(trip.idle_time for trip in trips)    # in minutes
                    # Filter out invalid trips (with 0 distance or speed)
                    valid_trips = [trip for trip in trips if trip.distance > 0 and trip.max_speed > 0]
                    
                    if valid_trips:
                        avg_speed = sum(trip.avg_speed * trip.distance for trip in valid_trips) / sum(trip.distance for trip in valid_trips)
                        max_speed = max(trip.max_speed for trip in valid_trips)
                        
                        # Update bc_speed with the calculated average speed
                        consumption_data["bc_speed"] = round(avg_speed, 1)
                        
                        # Convert minutes to hours and remaining minutes
                        drive_time_hours = total_drive_time // 60
                        drive_time_minutes = total_drive_time % 60
                        
                        consumption_data["note"] += (
                            f"\nTrip details:"
                            f"\n- Drive time: {drive_time_hours}h {drive_time_minutes}m"
                            f"\n- Idle time: {total_idle_time}m"
                            f"\n- Avg speed: {avg_speed:.1f} km/h"
                            f"\n- Max speed: {max_speed} km/h"
                            f"\n- Number of trips: {len(trips)}"
                        )

            # Send data to Spritmonitor
            response = self.spritmonitor.upload_consumption_data(
                vehicle_id=self.spritmonitor_vehicle_id,
                tank_id=self.spritmonitor_tank_id,
                data=consumption_data
            )
            self.logger.info(f"Successfully uploaded consumption data to Spritmonitor for {day_stats.date.strftime('%Y-%m-%d')}")
            
        except Exception as e:
            self.logger.error(f"Failed to upload consumption data to Spritmonitor: {str(e)}")
            raise
