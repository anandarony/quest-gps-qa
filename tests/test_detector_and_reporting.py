"""Defect detector + reporting integration tests."""
import pytest

from baseline.faulty_calculator import calculate_daily_distance
from src.defect_detector import ADVERTISER_RATE_USD_PER_KM, anomaly_scan, detect_defects
from src.reporting import build_before_after, financial_summary


def test_scan_counts_match_seed_manifest(pings):
    s = anomaly_scan(pings)
    assert (s["total_records"], s["rejected_records"], s["valid_records"]) == (2894, 200, 2694)
    assert s["duplicate_records"] == 72 and s["gps_noise_records"] == 72 and s["impossible_speed_records"] == 48
    assert s["invalid_field_records"] == 8


def test_all_defects_are_reproduced_and_ranked(pings):
    f = detect_defects(pings)
    assert {x.defect_id for x in f} == {"D-ORDER", "D-SPEED", "D-GAP", "D-NOISE", "D-ZONE", "D-DUP", "D-DAY", "D-PREC"}
    assert f[0].defect_id == "D-ORDER" and f[0].severity == "Critical"
    assert all(x.evidence for x in f)
    assert all(x.abs_driver_day_km > 0 for x in f)
    assert [x.abs_driver_day_km for x in f] == sorted((x.abs_driver_day_km for x in f), reverse=True)


def test_before_after_is_per_driver_day_and_money_is_consistent(pings):
    ba = build_before_after(pings, calculate_daily_distance(pings))
    assert len(ba) == 16
    assert (ba["difference_km"] - (ba["original_km"] - ba["corrected_km"])).abs().max() < 1e-5
    fs = financial_summary(ba)
    assert fs["original_km"] == pytest.approx(434.254, abs=1e-3) and fs["corrected_km"] == pytest.approx(134.324, abs=1e-3)
    assert fs["advertiser_overbilling_usd"] == pytest.approx(fs["difference_km"] * ADVERTISER_RATE_USD_PER_KM, abs=0.01)
    assert ba["reason_for_difference"].str.len().min() > 0
