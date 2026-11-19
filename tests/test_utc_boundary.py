"""UTC midnight / daily-cutoff tests (default cut 00:00 UTC; UTC+9 variant 00:00 JST = 15:00Z)."""
from datetime import datetime, timedelta, timezone

import pytest

from baseline.faulty_calculator import calculate_daily_distance
from helpers import mk, road_a, straight_trip
from src.distance_calculator import EngineConfig, driver_day_totals, process, split_by_report_day
from src.geo import haversine_m

UTC = timezone.utc
MID = datetime(2026, 3, 15, 0, 0, 0, tzinfo=UTC)


def _pair(t0, x0=3300, x1=3440):
    return mk([{"t": t0, "pos": road_a(x0)}, {"t": t0 + timedelta(seconds=10), "pos": road_a(x1)}])


def test_straddling_segment_is_split_pro_rata_by_time():
    df = _pair(MID - timedelta(seconds=4))                         # 4 s before / 6 s after midnight
    d = driver_day_totals(process(df).daily).set_index("report_date")["km"]
    chord = haversine_m(*road_a(3300), *road_a(3440)) / 1000
    assert d["2026-03-14"] == pytest.approx(chord * 0.4, abs=1e-12)
    assert d["2026-03-15"] == pytest.approx(chord * 0.6, abs=1e-12)
    assert d.sum() == pytest.approx(chord, abs=1e-12)


def test_ping_exactly_at_midnight_belongs_to_the_new_day():
    df = straight_trip([3300, 3440, 3580], start=MID - timedelta(seconds=10))
    d = driver_day_totals(process(df).daily).set_index("report_date")["km"]
    c1 = haversine_m(*road_a(3300), *road_a(3440)) / 1000
    c2 = haversine_m(*road_a(3440), *road_a(3580)) / 1000
    assert d["2026-03-14"] == pytest.approx(c1, abs=1e-12)
    assert d["2026-03-15"] == pytest.approx(c2, abs=1e-12)


def test_split_helper_multi_day_and_zero_length():
    pieces = dict(split_by_report_day(datetime(2026, 3, 13, 12, tzinfo=UTC).timestamp(),
                                      datetime(2026, 3, 15, 12, tzinfo=UTC).timestamp(), 48.0, 0.0))
    assert pieces == {"2026-03-13": pytest.approx(12.0), "2026-03-14": pytest.approx(24.0), "2026-03-15": pytest.approx(12.0)}
    t = MID.timestamp()
    assert split_by_report_day(t, t, 1.0, 0.0) == [("2026-03-15", 1.0)]


def test_jst_cut_is_1500_utc():
    cut = datetime(2026, 3, 14, 15, 0, 0, tzinfo=UTC)
    df = _pair(cut - timedelta(seconds=5))
    d = driver_day_totals(process(df, EngineConfig(report_utc_offset_hours=9)).daily).set_index("report_date")["km"]
    assert set(d.index) == {"2026-03-14", "2026-03-15"} and d["2026-03-14"] == pytest.approx(d["2026-03-15"], abs=1e-12)
    d0 = driver_day_totals(process(df).daily)
    assert list(d0["report_date"]) == ["2026-03-14"]               # UTC view: same day, no split


def test_same_instant_has_different_report_date_under_different_cut():
    df = _pair(datetime(2026, 3, 14, 20, 0, 0, tzinfo=UTC))
    assert driver_day_totals(process(df).daily)["report_date"].item() == "2026-03-14"
    assert driver_day_totals(process(df, EngineConfig(report_utc_offset_hours=9)).daily)["report_date"].item() == "2026-03-15"


def test_late_ping_across_cutoff_uses_its_own_timestamp_day():
    pre = MID - timedelta(seconds=20)
    df = straight_trip([3300, 3440, 3580, 3720], start=pre)
    df.loc[1, "ingest_ts_utc"] = (MID + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")   # arrives after midnight
    base = driver_day_totals(process(straight_trip([3300, 3440, 3580, 3720], start=pre)).daily)
    got = driver_day_totals(process(df.iloc[[0, 2, 3, 1]].reset_index(drop=True)).daily)
    assert got.set_index("report_date")["km"].round(12).to_dict() == base.set_index("report_date")["km"].round(12).to_dict()


def test_baseline_assigns_straddling_segment_to_one_day_only():
    df = _pair(MID - timedelta(seconds=4))
    legacy = calculate_daily_distance(df)
    assert set(legacy["report_date"]) == {"2026-03-15"}               # whole segment on the END ping's day
    assert set(driver_day_totals(process(df).daily)["report_date"]) == {"2026-03-14", "2026-03-15"}


@pytest.mark.parametrize("offset", [0.0, 9.0])
def test_dataset_daily_totals_match_oracle_across_cutoffs(pings, expected, offset):
    exp = expected[expected.report_utc_offset_hours == offset].groupby(["driver_id", "report_date"])["expected_km"].sum()
    got = driver_day_totals(process(pings, EngineConfig(report_utc_offset_hours=offset)).daily).set_index(["driver_id", "report_date"])["km"]
    assert set(exp.index) == set(got.index)
    assert (exp - got.reindex(exp.index)).abs().max() < 1e-6


def test_dataset_has_midnight_crossing_drivers(pings):
    d = driver_day_totals(process(pings).daily)
    assert (d.groupby("driver_id")["report_date"].nunique() == 2).sum() >= 4
    d9 = driver_day_totals(process(pings, EngineConfig(report_utc_offset_hours=9)).daily)
    assert (d9.groupby("driver_id")["report_date"].nunique() == 2).sum() >= 4
