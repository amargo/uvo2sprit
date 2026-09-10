"""Tests for the backfill orchestration: odometer walk-back, filtering and capping.

No network: the Spritmonitor client and the VehicleManager are replaced by fakes.
"""

import datetime
from types import SimpleNamespace

from VehicleClient import ChargeType, VehicleClient


class FakeSpritMonitor:
    def __init__(self, latest=None):
        self.latest = latest or []
        self.uploaded = []

    def get_latest_fuelings(self, vehicle_id, tank_id, limit=5):
        return self.latest

    def upload_consumption_data(self, vehicle_id, tank_id, data):
        self.uploaded.append(data)
        return {"ok": True}


class FakeVehicleManager:
    def __init__(self):
        self.day_calls = []
        self.month_calls = []

    def update_day_trip_info(self, vehicle_id, yyyymmdd):
        self.day_calls.append(yyyymmdd)

    def update_month_trip_info(self, vehicle_id, yyyymm):
        self.month_calls.append(yyyymm)


def make_day(date, distance, total_consumed=2000, regenerated_energy=500):
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


def make_client(daily_stats, odometer, latest=None, today=None, max_days=10):
    client = VehicleClient.__new__(VehicleClient)
    client.logger = __import__("logging").getLogger("test")
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
    client.max_days_per_run = max_days
    client.spritmonitor_vehicle_id = "123"
    client.spritmonitor_tank_id = "1"
    client.spritmonitor = FakeSpritMonitor(latest)
    client.vm = FakeVehicleManager()
    client.vehicle = SimpleNamespace(
        id="uuid",
        odometer=odometer,
        daily_stats=daily_stats,
        day_trip_info=None,
        ev_battery_percentage=55,
    )
    client._today = lambda: today or datetime.date(2025, 4, 14)
    return client


def days(*specs):
    return [make_day(datetime.datetime(*d), dist) for d, dist in specs]


class TestOdometerWalkBack:
    def test_odometer_counts_back_from_the_current_reading(self):
        stats = days(((2025, 4, 13), 100), ((2025, 4, 12), 50), ((2025, 4, 11), 25))
        client = make_client(stats, odometer=10000)

        client.process_and_upload_daily_stats()

        uploaded = client.spritmonitor.uploaded
        assert [u["date"] for u in uploaded] == ["11.04.2025", "12.04.2025", "13.04.2025"]
        # 13th ends at 10000, so the 12th ended at 9900 and the 11th at 9850
        assert [u["odometer"] for u in uploaded] == [9850, 9900, 10000]

    def test_trip_distance_matches_the_odometer_delta(self):
        stats = days(((2025, 4, 13), 100), ((2025, 4, 12), 50))
        client = make_client(stats, odometer=10000)

        client.process_and_upload_daily_stats()

        first, second = client.spritmonitor.uploaded
        assert second["odometer"] - first["odometer"] == second["trip"]


class TestFiltering:
    def test_today_is_never_uploaded(self):
        stats = days(((2025, 4, 14), 30), ((2025, 4, 13), 100))
        client = make_client(stats, odometer=10000, today=datetime.date(2025, 4, 14))

        client.process_and_upload_daily_stats()

        assert [u["date"] for u in client.spritmonitor.uploaded] == ["13.04.2025"]

    def test_days_already_in_spritmonitor_are_skipped(self):
        stats = days(((2025, 4, 13), 100), ((2025, 4, 12), 50), ((2025, 4, 11), 25))
        latest = [{"date": "12.04.2025", "odometer": "9900.00"}]
        client = make_client(stats, odometer=10000, latest=latest)

        client.process_and_upload_daily_stats()

        assert [u["date"] for u in client.spritmonitor.uploaded] == ["13.04.2025"]

    def test_nothing_to_do_makes_no_calls(self):
        stats = days(((2025, 4, 13), 100))
        latest = [{"date": "13.04.2025", "odometer": "10000.00"}]
        client = make_client(stats, odometer=10000, latest=latest)

        client.process_and_upload_daily_stats()

        assert client.spritmonitor.uploaded == []
        assert client.vm.day_calls == []

    def test_empty_daily_stats_is_handled(self):
        client = make_client([], odometer=10000)
        client.process_and_upload_daily_stats()
        assert client.spritmonitor.uploaded == []


class TestApiBudget:
    def test_backfill_is_capped_per_run(self):
        stats = days(*[((2025, 3, day), 10) for day in range(1, 21)])
        client = make_client(stats, odometer=10000, max_days=5)

        client.process_and_upload_daily_stats()

        assert len(client.spritmonitor.uploaded) == 5
        # oldest first, so the next run continues where this one stopped
        assert client.spritmonitor.uploaded[0]["date"] == "01.03.2025"
        assert client.spritmonitor.uploaded[-1]["date"] == "05.03.2025"

    def test_one_day_trip_call_each_and_one_month_call(self):
        stats = days(((2025, 4, 13), 100), ((2025, 4, 12), 50))
        client = make_client(stats, odometer=10000)

        client.process_and_upload_daily_stats()

        assert client.vm.day_calls == ["20250412", "20250413"]
        assert client.vm.month_calls == ["202504"]  # same month, fetched once

    def test_month_info_is_refetched_across_a_month_boundary(self):
        stats = days(((2025, 4, 1), 10), ((2025, 3, 31), 10))
        client = make_client(stats, odometer=10000)

        client.process_and_upload_daily_stats()

        assert client.vm.month_calls == ["202503", "202504"]


class TestResilience:
    def test_a_failing_day_does_not_abort_the_rest(self):
        stats = days(((2025, 4, 13), 100), ((2025, 4, 12), 50), ((2025, 4, 11), 25))
        client = make_client(stats, odometer=10000)

        original = client.spritmonitor.upload_consumption_data

        def flaky(vehicle_id, tank_id, data):
            if data["date"] == "12.04.2025":
                raise RuntimeError("Spritmonitor said no")
            return original(vehicle_id, tank_id, data)

        client.spritmonitor.upload_consumption_data = flaky

        client.process_and_upload_daily_stats()

        assert [u["date"] for u in client.spritmonitor.uploaded] == [
            "11.04.2025",
            "13.04.2025",
        ]

    def test_missing_trip_info_still_uploads_the_day(self):
        stats = days(((2025, 4, 13), 100))
        client = make_client(stats, odometer=10000)

        def boom(vehicle_id, yyyymmdd):
            raise RuntimeError("UVO trip info unavailable")

        client.vm.update_day_trip_info = boom

        client.process_and_upload_daily_stats()

        assert len(client.spritmonitor.uploaded) == 1

    def test_rate_limiting_stops_the_run_immediately(self):
        from hyundai_kia_connect_api.exceptions import RateLimitingError

        stats = days(((2025, 4, 13), 100), ((2025, 4, 12), 50), ((2025, 4, 11), 25))
        client = make_client(stats, odometer=10000)

        def rate_limited(vehicle_id, yyyymmdd):
            raise RateLimitingError("blocked")

        client.vm.update_day_trip_info = rate_limited

        client.process_and_upload_daily_stats()

        # nothing uploaded, and we did not keep hammering the API
        assert client.spritmonitor.uploaded == []

    def test_spritmonitor_read_failure_falls_back_to_full_backfill(self):
        stats = days(((2025, 4, 13), 100))
        client = make_client(stats, odometer=10000)

        def boom(vehicle_id, tank_id, limit=5):
            raise RuntimeError("Spritmonitor unreachable")

        client.spritmonitor.get_latest_fuelings = boom

        client.process_and_upload_daily_stats()

        assert len(client.spritmonitor.uploaded) == 1

    def test_no_vehicle_id_configured_skips_everything(self):
        stats = days(((2025, 4, 13), 100))
        client = make_client(stats, odometer=10000)
        client.spritmonitor_vehicle_id = None

        client.process_and_upload_daily_stats()

        assert client.spritmonitor.uploaded == []
