"""Impossible-speed tests: strictly above 150 km/h is rejected, measured against the last ACCEPTED ping."""
from datetime import timedelta

import pytest

from baseline.faulty_calculator import calculate_daily_distance
from helpers import T0, mk, road_a, straight_trip, x_for_chord
from src import gps_validator as gv
from src.distance_calculator import EngineConfig, process


def _two(kmh: float, dt: int = 10):
    x1 = 3000.0
    x2 = x_for_chord(x1, kmh / 3.6 * dt)
    return mk([{"t": T0, "pos": road_a(x1)}, {"t": T0 + timedelta(seconds=dt), "pos": road_a(x2)}])


@pytest.mark.parametrize("kmh,rejected", [(50, False), (149.9, False), (150.0, False), (150.5, True), (151.0, True), (300.0, True)])
def test_speed_boundary(kmh, rejected):
    a = process(_two(kmh)).audit
    assert (a.loc[1, "status"] == "REJECTED") is rejected
    assert a.loc[1, "implied_speed_kmh"] == pytest.approx(kmh, abs=0.05)
    if rejected:
        assert a.loc[1, "reject_reason"] == gv.R_IMPOSSIBLE_SPEED


def test_threshold_uses_real_elapsed_time():
    assert process(_two(150.0, dt=30)).audit.loc[1, "status"] == "ACCEPTED"      # 1.25 km in 30 s is exactly 150
    assert process(_two(180.0, dt=30)).audit.loc[1, "status"] == "REJECTED"


def test_first_ping_of_a_driver_is_never_speed_rejected():
    assert process(_two(50)).audit.loc[0, "status"] == "ACCEPTED"


def test_outlier_is_compared_to_last_accepted_and_does_not_poison_next_ping():
    xs = [3200 + 140 * i for i in range(7)]
    df = straight_trip(xs)
    df.loc[3, ["lat", "lon"]] = road_a(3200 + 140 * 3 + 1500)           # on-road teleport 1.5 km ahead
    a = process(df).audit
    assert a.loc[3, "reject_reason"] == gv.R_IMPOSSIBLE_SPEED
    assert (a.loc[[0, 1, 2, 4, 5, 6], "status"] == "ACCEPTED").all()
    clean_wo = process(straight_trip(xs).drop(index=3).reset_index(drop=True)).daily.km.sum()
    assert process(df).daily.km.sum() == pytest.approx(clean_wo, abs=1e-12)


def test_two_consecutive_bad_pings_both_rejected_and_good_neighbours_kept():
    xs = [3200 + 140 * i for i in range(8)]
    df = straight_trip(xs)
    df.loc[3, ["lat", "lon"]] = road_a(3200 + 140 * 3 + 1200)
    df.loc[4, ["lat", "lon"]] = road_a(3200 + 140 * 4 + 1300)
    a = process(df).audit
    assert (a.loc[[3, 4], "status"] == "REJECTED").all() and (a.loc[[0, 1, 2, 5, 6, 7], "status"] == "ACCEPTED").all()


def test_baseline_bills_teleport():
    xs = [3200 + 140 * i for i in range(7)]
    df = straight_trip(xs)
    df.loc[3, ["lat", "lon"]] = road_a(3200 + 140 * 3 + 1500)
    assert calculate_daily_distance(df)["km"].sum() > process(df).daily.km.sum() + 2.0
    assert process(df, EngineConfig(speed_filter=False)).daily.km.sum() > process(df).daily.km.sum() + 2.0


def test_suspicious_jump_flagged_even_when_accepted():
    a = process(_two(130.0)).audit
    assert a.loc[1, "status"] == "ACCEPTED" and bool(a.loc[1, "suspicious_jump"])


def test_dataset_speed_pings(labels, corrected_result):
    a = corrected_result.audit.merge(labels, on="ping_id").set_index("ping_id")
    jumps = a[a.seed_tag.isin(["SPEED_JUMP", "EDGE_150_5KMH_REJECT"])]
    assert len(jumps) == 48 and (jumps.reject_reason == gv.R_IMPOSSIBLE_SPEED).all()
    edge = a[a.seed_tag == "EDGE_150KMH_KEEP"]
    assert len(edge) == 12 and (edge.status == "ACCEPTED").all() and (edge.implied_speed_kmh.round(1) == 150.0).all()
