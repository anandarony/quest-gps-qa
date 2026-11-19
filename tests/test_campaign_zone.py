"""Campaign-zone tests: containment, clipping, entry/exit boundary crossings."""
import pytest

from baseline.faulty_calculator import calculate_daily_distance
from helpers import T0, ll, mk, road_a, road_a2, straight_trip
from datetime import timedelta
from src.campaign_zone import ZONES, Zone, zone_overlap_fractions, zones_containing
from src.distance_calculator import EngineConfig, process
from src.geo import haversine_m, to_xy

ZA = ZONES["ZONE_A"]            # rectangle x 3000..6500, y -500..1500
ZC = ZONES["ZONE_C"]            # convex pentagon


@pytest.mark.parametrize("p,inside", [((4000, 400), True), ((3000, 400), True), ((6500, 1500), True),
                                       ((2999.99, 400), False), ((6500.01, 400), False), ((4000, 1500.01), False)])
def test_point_in_zone_boundary_inclusive(p, inside):
    assert ZA.contains_xy(p) is inside


def test_pentagon_containment():
    assert ZC.contains_xy((7000, 9800)) and ZC.contains_xy((9000, 8700))        # vertex counts as inside
    assert not ZC.contains_xy((4400, 9500)) and not ZC.contains_xy((9900, 8800))


def test_clip_fraction_crossing_and_inside_and_outside():
    assert ZA.clip_fraction((2900, 400), (3100, 400)) == pytest.approx((0.5, 1.0))
    assert ZA.clip_fraction((3100, 400), (3300, 400)) == pytest.approx((0.0, 1.0))
    assert ZA.clip_fraction((1000, 400), (2000, 400)) is None
    t0, t1 = ZA.clip_fraction((2000, 400), (8000, 400))                          # passes straight through
    assert (t0, t1) == pytest.approx((1000 / 6000, 4500 / 6000))


def test_clip_touching_corner_has_zero_length():
    c = ZA.clip_fraction((2800, -700), (3000, -500))          # ends exactly on the corner (3000, -500)
    assert c is None or c[1] - c[0] < 1e-9
    assert zone_overlap_fractions((2800, -700), (3000, -500)) == {}      # zero-length overlaps are dropped


def test_segment_running_along_an_edge_is_inside():
    assert ZA.clip_fraction((3000, 0), (3000, 1000)) == pytest.approx((0.0, 1.0))


def test_zones_containing_lat_lon():
    assert zones_containing(*road_a(4000)) == ["ZONE_A"]
    assert zones_containing(*road_a(1000)) == []


def test_entry_segment_bills_only_inside_part():
    df = straight_trip([2900, 3100], step_s=10)                                  # crosses x=3000 at the middle
    chord = haversine_m(*road_a(2900), *road_a(3100)) / 1000
    km = process(df).daily.km.sum()
    assert km == pytest.approx(chord * 0.5, rel=1e-5)   # 7-decimal coordinates => ~1 cm placement precision
    assert calculate_daily_distance(df)["km"].sum() == pytest.approx(chord, rel=0.02)    # baseline bills it all


def test_exit_segment_bills_only_inside_part():
    df = mk([{"t": T0, "pos": road_a2(6400)}, {"t": T0 + timedelta(seconds=10), "pos": road_a2(6600)}])
    chord = haversine_m(*road_a2(6400), *road_a2(6600)) / 1000
    assert process(df).daily.km.sum() == pytest.approx(chord * 0.5, rel=1e-3)
    assert calculate_daily_distance(df)["km"].sum() == 0.0                       # baseline: END ping outside -> nothing


def test_ping_exactly_on_boundary_splits_without_double_counting():
    on_edge = ll(3000, 400)
    df = mk([{"t": T0, "pos": ll(2900, 390)}, {"t": T0 + timedelta(seconds=10), "pos": on_edge},
             {"t": T0 + timedelta(seconds=20), "pos": ll(3100, 410)}])
    seg = process(df).segments
    assert seg.loc[0, "in_zone_m"] < 0.02            # at most ~1 cm of coordinate rounding
    assert seg.loc[1, "in_zone_m"] == pytest.approx(seg.loc[1, "chord_m"], abs=0.02)


def test_entry_plus_exit_in_one_trip_sums_to_inside_length():
    xs = [2900, 3100, 3300]
    df = straight_trip(xs)
    ins = process(df).daily.km.sum()
    expect = (haversine_m(*road_a(2900), *road_a(3100)) * 0.5 + haversine_m(*road_a(3100), *road_a(3300))) / 1000
    assert ins == pytest.approx(expect, rel=1e-5)


def test_vehicle_outside_every_zone_bills_nothing():
    assert process(straight_trip([-900, -760, -620])).daily.empty


def test_zone_attribution_per_zone():
    df = straight_trip([3300, 3440, 3580])
    assert set(process(df).daily.zone_id) == {"ZONE_A"}


def test_endpoint_ablation_overbills_entry():
    df = straight_trip([2900, 3100])
    assert process(df, EngineConfig(zone_mode="endpoint")).daily.km.sum() > process(df).daily.km.sum() * 1.9


def test_dataset_zone_totals_match_oracle_per_zone(pings, expected):
    got = process(pings).daily.set_index(["driver_id", "report_date", "zone_id"])["km"]
    exp = expected[expected.report_utc_offset_hours == 0].set_index(["driver_id", "report_date", "zone_id"])["expected_km"]
    assert set(got.index) == set(exp.index)
    assert (got.reindex(exp.index) - exp).abs().max() < 1e-6
    assert set(exp.index.get_level_values("zone_id")) == {"ZONE_A", "ZONE_B", "ZONE_C"}
