"""Regression suite.

Acceptance criteria covered here
  1. the regression test FAILS against the original defective implementation,
  2. the SAME test PASSES against the corrected implementation,
  3. corrected output matches the independent oracle (expected_results.csv),
  4. previously valid scenarios still behave the same.

Select the implementation under test with the env var  GPS_IMPL=corrected|baseline
(default: corrected).  The meta-tests at the bottom run the SAME oracle test in a
subprocess against both implementations and assert fail/pass respectively.
"""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from baseline.faulty_calculator import calculate_daily_distance
from helpers import road_a, straight_trip
from src.distance_calculator import EngineConfig, driver_day_totals, process
from src.geo import haversine_m

ROOT = Path(__file__).resolve().parents[1]
IMPL = os.environ.get("GPS_IMPL", "corrected")
TOL_KM = 1e-6


def run_impl(pings: pd.DataFrame, offset: float = 0.0) -> pd.DataFrame:
    if IMPL == "baseline":
        return calculate_daily_distance(pings, offset)
    if IMPL == "corrected":
        return process(pings, EngineConfig(report_utc_offset_hours=offset)).daily
    raise ValueError(IMPL)


def _compare(got: pd.DataFrame, exp: pd.DataFrame) -> pd.DataFrame:
    m = got.merge(exp, on=["driver_id", "report_date", "zone_id"], how="outer").fillna({"km": 0.0, "expected_km": 0.0})
    m["abs_err_km"] = (m["km"] - m["expected_km"]).abs()
    return m


@pytest.mark.parametrize("offset", [0.0, 9.0])
def test_matches_oracle_all_days(pings, expected, offset):
    """THE regression test: every driver/day/zone equals the independent oracle."""
    exp = expected[expected.report_utc_offset_hours == offset][["driver_id", "report_date", "zone_id", "expected_km"]]
    m = _compare(run_impl(pings, offset), exp)
    bad = m[m.abs_err_km > TOL_KM]
    assert bad.empty, f"{len(bad)} driver/day/zone rows differ from the oracle; worst {bad.abs_err_km.max():.3f} km\n{bad.head()}"


def test_total_matches_oracle(pings, expected):
    exp = expected[expected.report_utc_offset_hours == 0.0]["expected_km"].sum()
    assert run_impl(pings).km.sum() == pytest.approx(exp, abs=1e-5)


# ----- the corrected engine only (these do not depend on GPS_IMPL) --------------------------------------
def test_baseline_is_far_from_oracle(pings, expected):
    exp = expected[expected.report_utc_offset_hours == 0.0]["expected_km"].sum()
    assert calculate_daily_distance(pings).km.sum() > exp * 2.5          # 434 km vs 134 km on the seeded data


def test_ablation_of_all_rules_reproduces_baseline_exactly(pings):
    """Validates the defect attribution: engine with every rule disabled == faulty baseline."""
    legacy = EngineConfig(dedupe=False, ordering="arrival", offroad_filter=False, speed_filter=False, gap_rule=False,
                          zone_mode="endpoint", day_mode="end", coord_decimals=4)
    a = process(pings, legacy).daily
    b = calculate_daily_distance(pings)
    m = a.merge(b, on=["driver_id", "report_date", "zone_id"], how="outer", suffixes=("_a", "_b")).fillna(0)
    assert (m.km_a - m.km_b).abs().max() < 1e-9


def test_previously_valid_scenario_is_unchanged():
    """A clean, sorted, de-duplicated, fully-inside-zone trip: corrected == plain sum of chords
    and the old baseline agreed with it (to its 4-decimal precision)."""
    xs = [3200 + 90 * i for i in range(20)]        # stays on ROAD_A leg 1 (x <= 5000)
    df = straight_trip(xs)
    plain = sum(haversine_m(*road_a(a), *road_a(b)) for a, b in zip(xs, xs[1:])) / 1000
    assert process(df).daily.km.sum() == pytest.approx(plain, rel=1e-12)
    assert calculate_daily_distance(df).km.sum() == pytest.approx(plain, rel=0.02)


def test_clean_driver_days_unchanged_by_the_fix(pings, labels):
    """Pings with no seeded defect in their neighbourhood produce the same result before/after for the
    legacy-compatible rules: restrict to CLEAN rows of one midday driver and compare segment-wise."""
    clean_ids = set(labels.loc[labels.seed_tag == "CLEAN", "ping_id"])
    sub = pings[pings.ping_id.isin(clean_ids) & (pings.driver_id == "D009")].copy()
    sub = sub.sort_values("ts_utc")                           # sorted + only clean rows
    new = process(sub).daily.km.sum()
    cfg = EngineConfig(gap_rule=False, coord_decimals=None)   # same rules the baseline implicitly had on clean data
    assert new == pytest.approx(process(sub, cfg).daily.km.sum(), rel=0.2)
    assert new > 0


def test_deterministic_repeat_runs(pings):
    a, b = process(pings).daily, process(pings).daily
    pd.testing.assert_frame_equal(a, b)


def test_engine_has_no_hardcoded_expected_results():
    src = (ROOT / "src" / "distance_calculator.py").read_text() + (ROOT / "src" / "gps_validator.py").read_text()
    for forbidden in ("expected_results", "ping_labels", "seeded_defects", "truth_accept"):
        assert forbidden not in src


@pytest.mark.parametrize("seed", [1, 7, 20260101])
def test_engine_matches_oracle_on_other_seeds(seed):
    """Not overfitted to one dataset: regenerate with other seeds and compare with a fresh oracle run."""
    from src.data_generator import generate_dataset
    from src.oracle import expected_results
    p, l, _ = generate_dataset(seed)
    pp = p.copy()
    pp["lat"] = pd.to_numeric(pp["lat"], errors="coerce"); pp["lon"] = pd.to_numeric(pp["lon"], errors="coerce")
    for off in (0.0, 9.0):
        exp = expected_results(pp, l.astype(str), off)
        m = _compare(process(p.astype(str), EngineConfig(report_utc_offset_hours=off)).daily, exp[["driver_id", "report_date", "zone_id", "expected_km"]])
        assert m.abs_err_km.max() < TOL_KM, (seed, off)


# ----- meta-tests: prove the regression test discriminates ------------------------------------------------
def _run_oracle_test(impl: str):
    env = {**os.environ, "GPS_IMPL": impl}
    return subprocess.run([sys.executable, "-m", "pytest", "tests/test_regression.py::test_matches_oracle_all_days",
                           "-q", "-p", "no:cacheprovider"], cwd=ROOT, env=env, capture_output=True, text=True)


def test_meta_regression_test_FAILS_on_baseline():
    r = _run_oracle_test("baseline")
    assert r.returncode != 0, r.stdout
    assert "2 failed" in r.stdout, r.stdout[-500:]


def test_meta_regression_test_PASSES_on_corrected():
    r = _run_oracle_test("corrected")
    assert r.returncode == 0, r.stdout[-800:]
    assert "2 passed" in r.stdout
