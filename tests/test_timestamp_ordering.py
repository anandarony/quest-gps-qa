"""Timestamp ordering, out-of-order and late-arriving ping tests."""
from datetime import timedelta

import numpy as np

from baseline.faulty_calculator import calculate_daily_distance
from helpers import T0, mk, road_a, straight_trip
from src.distance_calculator import EngineConfig, process

XS = [3100 + 140 * i for i in range(10)]


def test_row_order_does_not_change_result():
    df = straight_trip(XS)
    ref = process(df).daily.km.sum()
    for seed in range(5):
        shuffled = df.iloc[np.random.default_rng(seed).permutation(len(df))].reset_index(drop=True)
        assert abs(process(shuffled).daily.km.sum() - ref) < 1e-12


def test_late_ping_is_placed_by_its_own_timestamp():
    df = straight_trip(XS)
    ontime = process(df).daily.km.sum()
    late = df.copy()
    late.loc[4, "ingest_ts_utc"] = (T0 + timedelta(seconds=40 + 600)).strftime("%Y-%m-%dT%H:%M:%SZ")
    late = pd_move_to_end(late, 4)                      # it also arrives last in the file
    assert abs(process(late).daily.km.sum() - ontime) < 1e-12


def pd_move_to_end(df, i):
    import pandas as pd
    return pd.concat([df.drop(index=i), df.loc[[i]]], ignore_index=True)


def test_baseline_arrival_order_inflates_distance_for_late_ping():
    df = pd_move_to_end(straight_trip(XS), 3)
    good = process(df).daily.km.sum()
    bad = calculate_daily_distance(df)["km"].sum()
    assert bad > good * 1.5, (good, bad)


def test_arrival_order_ablation_is_the_cause():
    df = pd_move_to_end(straight_trip(XS), 3)
    legacy = process(df, EngineConfig(ordering="arrival")).daily.km.sum()
    assert legacy > process(df).daily.km.sum() * 1.5


def test_out_of_order_arrival_is_flagged_but_accepted():
    df = pd_move_to_end(straight_trip(XS), 3)
    a = process(df).audit
    assert a["arrived_out_of_order"].sum() == 1
    assert (a["status"] == "ACCEPTED").all()


def test_ingest_delay_recorded_in_audit():
    df = straight_trip(XS)
    df.loc[2, "ingest_ts_utc"] = (T0 + timedelta(seconds=20 + 900)).strftime("%Y-%m-%dT%H:%M:%SZ")
    a = process(df).audit
    assert a.loc[2, "ingest_delay_s"] == 900


def test_accepted_pings_are_sorted_by_event_time(pings):
    acc = process(pings).accepted
    for _, g in acc.groupby("driver_id"):
        assert g["ts"].is_monotonic_increasing


def test_dataset_shuffle_invariance(pings):
    base = process(pings).daily
    shuf = pings.iloc[np.random.default_rng(42).permutation(len(pings))].reset_index(drop=True)
    got = process(shuf).daily
    m = base.merge(got, on=["driver_id", "report_date", "zone_id"], suffixes=("_a", "_b"))
    assert len(m) == len(base) == len(got)
    assert (m["km_a"] - m["km_b"]).abs().max() < 1e-9


def test_dataset_contains_late_and_out_of_order_rows(pings):
    a = process(pings).audit
    assert a["arrived_out_of_order"].sum() >= 100
    assert (a["ingest_delay_s"] > 100).sum() >= 100
