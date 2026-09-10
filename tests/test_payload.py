"""Tests for the payload building, which is where the unit bugs used to live.

Values are taken from example/daily_stats.json and example/day_trip_info.json.
"""

import datetime
from types import SimpleNamespace

import pytest

from SpritMonitorClient import SpritMonitorClient
from VehicleClient import ChargeType, VehicleClient


def make_client(**overrides):
    """A VehicleClient with no network: __init__ would log in to the UVO API."""
    client = VehicleClient.__new__(VehicleClient)
    client.country = "HU"
    client.station_name = "home"
    client.fuelsort_id = "19"
    client.quantity_mode = "gross"
    client.spritmonitor_force_full_percent = True
    client.send_live_charge_info = False
    client.charge_type = ChargeType.UNKNOWN
    client.charging_power_in_kilowatts = 0.0
    client.electricity_price = None
    client.currency_id = 11
    client.battery_total_kwh = 70.0
    client.vehicle = SimpleNamespace(day_trip_info=None, ev_battery_percentage=55, odometer=100000)
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def make_day(
    *,
    date=datetime.datetime(2025, 4, 12),
    total_consumed=1728,
    regenerated_energy=1267,
    distance=16,
):
    return SimpleNamespace(
        date=date,
        total_consumed=total_consumed,
        engine_consumption=1379,
        climate_consumption=49,
        onboard_electronics_consumption=300,
        battery_care_consumption=0,
        regenerated_energy=regenerated_energy,
        distance=distance,
        distance_unit="km",
    )


def make_trip(distance, avg_speed, max_speed, drive_time=60, idle_time=5):
    return SimpleNamespace(
        distance=distance,
        avg_speed=avg_speed,
        max_speed=max_speed,
        drive_time=drive_time,
        idle_time=idle_time,
    )


class TestConsumptionPayload:
    def test_gross_quantity_and_kwh_per_100km(self):
        # 192 km on 25.45 kWh -> 13.3 kWh/100km, the README worked example
        day = make_day(total_consumed=25450, regenerated_energy=7650, distance=192)
        payload = make_client().build_consumption_payload(day, 100000)

        # round() is banker's rounding: 25.45 -> 25.4
        assert payload["quantity"] == 25.4  # gross, in kWh
        assert payload["bc_consumption"] == 13.3  # kWh/100km, NOT km/kWh
        assert payload["trip"] == 192
        assert payload["date"] == "12.04.2025"

    def test_net_quantity_mode(self):
        day = make_day(total_consumed=25450, regenerated_energy=7650, distance=192)
        payload = make_client(quantity_mode="net").build_consumption_payload(day, 100000)

        assert payload["quantity"] == 17.8  # (25450 - 7650) / 1000
        assert payload["bc_quantity"] == payload["quantity"]  # same basis

    def test_zero_distance_does_not_divide_by_zero(self):
        day = make_day(total_consumed=140, regenerated_energy=0, distance=0)
        payload = make_client().build_consumption_payload(day, 100000)

        assert payload["bc_consumption"] == 0

    def test_odometer_is_an_integer(self):
        payload = make_client().build_consumption_payload(make_day(), 123456.7)
        assert payload["odometer"] == 123456

    def test_country_and_station_are_present(self):
        payload = make_client().build_consumption_payload(make_day(), 100000)
        assert payload["country"] == "HU"
        assert payload["stationname"] == "home"

    def test_live_charge_info_is_off_by_default(self):
        client = make_client(charge_type=ChargeType.AC, charging_power_in_kilowatts=7.2)
        payload = client.build_consumption_payload(make_day(), 100000)

        assert "charge_info" not in payload
        assert "charging_power" not in payload

    def test_live_charge_info_can_be_enabled(self):
        client = make_client(
            send_live_charge_info=True,
            charge_type=ChargeType.AC,
            charging_power_in_kilowatts=7.2,
        )
        payload = client.build_consumption_payload(make_day(), 100000)

        assert payload["charge_info"] == "ac"  # no source_vehicle yet, that's the client
        assert payload["charging_power"] == 7.2

    def test_price_only_when_configured(self):
        assert "price" not in make_client().build_consumption_payload(make_day(), 1000)

        client = make_client(electricity_price=41.0)
        payload = client.build_consumption_payload(make_day(), 1000)
        assert payload["price"] == 41.0
        assert payload["currencyid"] == 11
        assert payload["pricetype"] == 1

    def test_percent_forced_to_full_by_default(self):
        assert make_client().build_consumption_payload(make_day(), 1000)["percent"] == 100

        client = make_client(spritmonitor_force_full_percent=False)
        assert client.build_consumption_payload(make_day(), 1000)["percent"] == 55

    def test_note_contains_the_energy_breakdown(self):
        note = make_client().build_consumption_payload(make_day(), 1000)["note"]
        assert "Gross: 1.7 kWh" in note
        assert "Net: 0.5 kWh" in note
        assert "Regenerated: 1.3 kWh" in note


class TestTripSummary:
    def test_avg_speed_is_distance_weighted(self):
        trips = [make_trip(79, 88, 144), make_trip(76, 74, 134), make_trip(24, 46, 99)]
        client = make_client()
        client.vehicle.day_trip_info = SimpleNamespace(trip_list=trips)

        payload = client.build_consumption_payload(make_day(), 100000)

        expected = (88 * 79 + 74 * 76 + 46 * 24) / (79 + 76 + 24)
        assert payload["bc_speed"] == round(expected, 1)
        assert "Max speed: 144 km/h" in payload["note"]
        assert "Number of trips: 3" in payload["note"]

    def test_trips_without_distance_are_ignored(self):
        client = make_client()
        client.vehicle.day_trip_info = SimpleNamespace(
            trip_list=[make_trip(0, 0, 0), make_trip(0, 0, 0)]
        )

        payload = client.build_consumption_payload(make_day(), 100000)

        assert payload["bc_speed"] == 0
        assert "Trip details" not in payload["note"]


class TestChargingPowerEstimate:
    def _client(self, **vehicle_attrs):
        client = make_client()
        client.logger = __import__("logging").getLogger("test")
        client.vehicle = SimpleNamespace(**vehicle_attrs)
        return client

    def test_not_charging_resets_state(self):
        client = self._client(ev_battery_is_charging=False)
        client.charge_type = ChargeType.DC
        client.charging_power_in_kilowatts = 50.0

        assert client.get_estimated_charging_power() == 0.0
        assert client.charging_power_in_kilowatts == 0.0
        assert client.charge_type is ChargeType.UNKNOWN

    def test_missing_charge_duration_does_not_crash(self):
        client = self._client(
            ev_battery_is_charging=True,
            ev_estimated_current_charge_duration=0,
            ev_battery_percentage=50,
            ev_charge_limits_ac=100,
            ev_charge_limits_dc=100,
        )
        assert client.get_estimated_charging_power() == 0.0

    def test_ac_charging_sets_the_power_attribute(self):
        # 20% missing of 70 kWh = 14 kWh over 120 min -> 7 kW, below the 8 kW AC cut-off
        client = self._client(
            ev_battery_is_charging=True,
            ev_estimated_current_charge_duration=120,
            ev_battery_percentage=80,
            ev_charge_limits_ac=100,
            ev_charge_limits_dc=100,
        )
        power = client.get_estimated_charging_power()

        assert client.charge_type is ChargeType.AC
        assert power == 7.0
        # regression: the attribute used to stay 0 on the AC path
        assert client.charging_power_in_kilowatts == 7.0

    def test_dc_charging_is_capped_by_the_power_curve(self):
        # 70% missing of 70 kWh = 49 kWh over 30 min -> 98 kW, capped to 77 at 30% SoC
        client = self._client(
            ev_battery_is_charging=True,
            ev_estimated_current_charge_duration=30,
            ev_battery_percentage=30,
            ev_charge_limits_ac=100,
            ev_charge_limits_dc=100,
        )
        power = client.get_estimated_charging_power()

        assert client.charge_type is ChargeType.DC
        assert power == 77.0


class TestSpritMonitorParams:
    def client(self):
        return SpritMonitorClient(bearer_token="bearer", app_token="app")

    def base_payload(self, **extra):
        payload = {
            "date": "12.04.2025",
            "odometer": 100000,
            "trip": 16,
            "quantity": 1.7,
            "type": "full",
            "quantityunitid": 5,
            "percent": 100,
        }
        payload.update(extra)
        return payload

    def test_source_vehicle_is_appended_exactly_once(self):
        params = self.client().build_consumption_params(self.base_payload(charge_info="ac"))
        assert params["charge_info"] == "ac,source_vehicle"

    def test_no_charge_info_means_no_key(self):
        params = self.client().build_consumption_params(self.base_payload())
        assert "charge_info" not in params

    def test_country_and_station_are_forwarded(self):
        params = self.client().build_consumption_params(
            self.base_payload(country="HU", stationname="home")
        )
        assert params["country"] == "HU"
        assert params["stationname"] == "home"

    def test_fuelsortid_defaults_to_electricity(self):
        params = self.client().build_consumption_params(self.base_payload())
        assert params["fuelsortid"] == "19"

        params = self.client().build_consumption_params(self.base_payload(fuelsortid="24"))
        assert params["fuelsortid"] == "24"

    def test_empty_optional_values_are_dropped(self):
        params = self.client().build_consumption_params(
            self.base_payload(note="", location="", charging_power=0)
        )
        assert "note" not in params
        assert "location" not in params
        assert "charging_power" not in params

    def test_missing_tokens_are_rejected(self):
        from SpritMonitorClient import SpritMonitorError

        with pytest.raises(SpritMonitorError):
            SpritMonitorClient(bearer_token="", app_token="app")
        with pytest.raises(SpritMonitorError):
            SpritMonitorClient(bearer_token="bearer", app_token="")
