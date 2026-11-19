"""GPS noise (distance-to-road) tests: strictly more than 100 m is rejected."""
import pytest

from baseline.faulty_calculator import calculate_daily_distance
from helpers import T0, mk, offset_from_road_a, road_a, straight_trip
from src import gps_validator as gv
from src.distance_calculator import EngineConfig, process
from src.geo import distance_to_road_network

XS = [3200 + 140 * i for i in range(7)]


def _trip_with_offset(metres: float, i: int = 3):
    df = straight_trip(XS)
    df.loc[i, ["lat", "lon"]] = offset_from_road_a(XS[i], metres)
    return df


@pytest.mark.parametrize("metres,rejected", [(0.0, False), (99.9, False), (100.0, False), (100.5, True), (101.0, True), (250.0, True), (600.0, True)])
def test_offset_boundary(metres, rejected):
    a = process(_trip_with_offset(metres)).audit
    assert (a.loc[3, "status"] == "REJECTED") is rejected
    if rejected:
        assert a.loc[3, "reject_reason"] == gv.R_OFF_ROAD


def test_helper_builds_expected_offsets():
    for m in (99.9, 100.0, 100.5):
        d = distance_to_road_network(*offset_from_road_a(3500, m))
        assert d == pytest.approx(m, abs=0.02)


def test_rejected_noise_ping_does_not_change_distance():
    noisy = process(_trip_with_offset(300.0)).daily.km.sum()
    without = process(straight_trip(XS).drop(index=3).reset_index(drop=True)).daily.km.sum()
    assert noisy == pytest.approx(without, abs=1e-12)


def test_noise_within_tolerance_is_kept_and_counted():
    kept = process(_trip_with_offset(100.0)).daily.km.sum()
    clean = process(straight_trip(XS)).daily.km.sum()
    assert kept > clean                       # the 100 m detour is legitimately billed (rule says only > 100 m is ignored)


def test_far_away_valid_coordinates_are_noise():
    df = _trip_with_offset(0.0)
    df.loc[3, ["lat", "lon"]] = (0.0, 0.0)
    assert process(df).audit.loc[3, "reject_reason"] == gv.R_OFF_ROAD


def test_baseline_bills_noise_detour():
    # 200 m offset => 244 m in 10 s = 88 km/h, so ONLY the off-road rule can catch it (a 400 m offset would also trip the speed rule)
    df = _trip_with_offset(200.0)
    assert calculate_daily_distance(df)["km"].sum() > process(df).daily.km.sum() + 0.15
    assert process(df, EngineConfig(offroad_filter=False)).daily.km.sum() > process(df).daily.km.sum() + 0.15


def test_offroad_distance_recorded_in_audit():
    a = process(_trip_with_offset(300.0)).audit
    assert a.loc[3, "offroad_m"] == pytest.approx(300.0, abs=0.05)


def test_dataset_noise_and_edge_pings(labels, corrected_result):
    a = corrected_result.audit.merge(labels, on="ping_id").set_index("ping_id")
    noise = a[a.seed_tag.isin(["NOISE_OFFROAD", "EDGE_100_5M_REJECT"])]
    assert len(noise) == 72 and (noise.reject_reason == gv.R_OFF_ROAD).all()
    keep = a[a.seed_tag == "EDGE_100M_KEEP"]
    assert len(keep) == 12 and (keep.status == "ACCEPTED").all()
    assert (keep.offroad_m.round(1) == 100.0).all()
