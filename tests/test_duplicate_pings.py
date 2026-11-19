"""Duplicate ping tests."""
from datetime import timedelta

import numpy as np
import pandas as pd

from baseline.faulty_calculator import calculate_daily_distance
from helpers import T0, ll, mk, road_a, straight_trip
from src import gps_validator as gv
from src.distance_calculator import EngineConfig, process


def _trip_with_duplicate(offset_m: float):
    rows = [{"t": T0 + timedelta(seconds=10 * i), "pos": road_a(3200 + 140 * i), "pid": f"A{i}"} for i in range(6)]
    x, y = 3200 + 140 * 2, 0.1 * (3200 + 140 * 2 + 1000)
    rows.insert(3, {"t": T0 + timedelta(seconds=20), "pos": ll(x, y + offset_m), "pid": "DUP", "ingest": T0 + timedelta(seconds=23)})
    return mk(rows)


def test_same_driver_same_timestamp_is_collapsed():
    r = process(_trip_with_duplicate(30))
    assert r.audit.loc[r.audit.ping_id == "DUP", "reject_reason"].item() == gv.R_DUPLICATE
    assert (r.audit["status"] == "ACCEPTED").sum() == 6


def test_collapsed_duplicate_does_not_change_distance():
    clean = process(_trip_with_duplicate(0).query("ping_id != 'DUP'")).daily.km.sum()
    dirty = process(_trip_with_duplicate(30)).daily.km.sum()
    assert dirty == clean


def test_same_timestamp_different_drivers_are_not_duplicates():
    df = pd.concat([straight_trip([3300, 3400], driver="D1"), straight_trip([3300, 3400], driver="D2")], ignore_index=True)
    df["ping_id"] = [f"X{i}" for i in range(len(df))]
    r = process(df)
    assert (r.audit["status"] == "ACCEPTED").all()


def test_earliest_ingest_wins_regardless_of_row_order():
    a = mk([{"t": T0, "pos": road_a(3300), "ingest": T0 + timedelta(seconds=5), "pid": "LATE_ARRIVER"},
            {"t": T0, "pos": road_a(3310), "ingest": T0 + timedelta(seconds=2), "pid": "EARLY_ARRIVER"}])
    for df in (a, a.iloc[::-1].reset_index(drop=True)):
        r = process(df)
        assert r.audit.set_index("ping_id").loc["EARLY_ARRIVER", "status"] == "ACCEPTED"
        assert r.audit.set_index("ping_id").loc["LATE_ARRIVER", "reject_reason"] == gv.R_DUPLICATE


def test_tie_on_ingest_resolved_by_smaller_ping_id_deterministically():
    base = mk([{"t": T0, "pos": road_a(3300), "ingest": T0, "pid": "P2"},
               {"t": T0, "pos": road_a(3310), "ingest": T0, "pid": "P1"}])
    for df in (base, base.iloc[::-1].reset_index(drop=True)):
        s = process(df).audit.set_index("ping_id")
        assert s.loc["P1", "status"] == "ACCEPTED" and s.loc["P2", "status"] == "REJECTED"


def test_triplicates_keep_exactly_one():
    df = mk([{"t": T0, "pos": road_a(3300), "ingest": T0 + timedelta(seconds=i), "pid": f"T{i}"} for i in range(3)])
    assert (process(df).audit["status"] == "ACCEPTED").sum() == 1


def test_dataset_duplicate_count_matches_seed(pings, corrected_result, labels):
    n_seed = int((labels["seed_tag"] == "DUP_DROPPED").sum())
    assert n_seed == 72
    assert int((corrected_result.audit["reject_reason"] == gv.R_DUPLICATE).sum()) == n_seed


def test_shuffling_rows_does_not_change_duplicate_decisions(pings):
    rng = np.random.default_rng(1)
    base = process(pings).audit.set_index("ping_id")["status"]
    shuf = pings.iloc[rng.permutation(len(pings))].reset_index(drop=True)
    got = process(shuf).audit.set_index("ping_id")["status"]
    assert (base.sort_index() == got.sort_index()).all()


def test_regression_baseline_double_counts_jittered_duplicate():
    """Negative/regression: the defective baseline inflates distance, the engine does not."""
    df = _trip_with_duplicate(40)
    good = process(df).daily.km.sum()
    bad = calculate_daily_distance(df)["km"].sum()
    assert bad > good + 0.03      # a 40 m zig-zag appears twice
    nodedupe = process(df, EngineConfig(dedupe=False)).daily.km.sum()
    assert nodedupe > good
