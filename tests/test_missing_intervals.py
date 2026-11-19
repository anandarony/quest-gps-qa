"""Missing GPS intervals / irregular sampling (assumption A3: gaps > 30 s are not billed)."""
from datetime import timedelta

import pytest

from baseline.faulty_calculator import calculate_daily_distance
from helpers import T0, mk, road_a
from src.distance_calculator import EngineConfig, process
from src.geo import haversine_m


def _gap_trip(dt_s: int):
    return mk([{"t": T0, "pos": road_a(3200)}, {"t": T0 + timedelta(seconds=10), "pos": road_a(3340)},
               {"t": T0 + timedelta(seconds=10 + dt_s), "pos": road_a(3480)},
               {"t": T0 + timedelta(seconds=20 + dt_s), "pos": road_a(3620)}])


@pytest.mark.parametrize("dt,billed", [(10, True), (20, True), (30, True), (31, False), (90, False), (300, False)])
def test_gap_boundary_30_seconds(dt, billed):
    seg = process(_gap_trip(dt)).segments
    assert bool(seg.loc[1, "gap_not_billed"]) is (not billed)
    assert bool(seg.loc[1, "in_zone_m"] > 0) is billed


def test_gap_segment_removed_but_neighbours_still_billed():
    km = process(_gap_trip(90)).daily.km.sum()
    expect = (haversine_m(*road_a(3200), *road_a(3340)) + haversine_m(*road_a(3480), *road_a(3620))) / 1000
    assert km == pytest.approx(expect, rel=1e-9)


def test_baseline_bills_across_the_gap():
    assert calculate_daily_distance(_gap_trip(300))["km"].sum() > process(_gap_trip(300)).daily.km.sum()
    assert process(_gap_trip(300), EngineConfig(gap_rule=False)).daily.km.sum() > process(_gap_trip(300)).daily.km.sum()


def test_dataset_gaps(pings, corrected_result):
    seg = corrected_result.segments
    assert int(seg["gap_not_billed"].sum()) == 18          # 12 x 90 s + 6 x 300 s (30 s edge gaps ARE billed)
    assert int(((seg["dt_s"] == 30.0) & ~seg["gap_not_billed"]).sum()) >= 12
