"""Data validation tests (validator + dataset integrity)."""
import json
from datetime import timedelta

import pandas as pd
import pytest

from helpers import T0, mk, road_a
from src import gps_validator as gv
from src.distance_calculator import process


def _one(**over):
    row = {"pos": road_a(3500), "t": T0}
    row.update(over)
    return mk([row])


# ---------------------------------------------------------------- unit
def test_valid_row_passes():
    r = process(_one())
    assert r.audit["status"].tolist() == ["ACCEPTED"]


@pytest.mark.parametrize("lat,lon", [(90.0001, 139.7), (-90.5, 139.7), (35.6, 180.01), (35.6, -181)])
def test_out_of_range_coordinates_rejected(lat, lon):
    r = process(_one(pos=(lat, lon)))
    assert r.audit.iloc[0]["reject_reason"] == gv.R_INVALID_COORDINATES


@pytest.mark.parametrize("lat,lon", [(90.0, 139.7), (35.6, 180.0), (-90.0, -180.0)])
def test_range_limits_are_inclusive(lat, lon):
    norm = gv.normalise(_one(pos=(lat, lon)))
    assert gv.validate_fields(norm).isna().all()


def test_non_numeric_coordinate_rejected():
    r = process(_one(pos=("abc", 139.7)))
    assert r.audit.iloc[0]["reject_reason"] == gv.R_INVALID_COORDINATES


@pytest.mark.parametrize("bad_ts", ["not-a-time", "2026-13-45T25:61:00Z", "14/03/2026"])
def test_invalid_timestamp_rejected(bad_ts):
    r = process(_one(t=bad_ts))
    assert r.audit.iloc[0]["reject_reason"] == gv.R_INVALID_TIMESTAMP


@pytest.mark.parametrize("field", ["driver_id", "ts_utc", "lat", "lon", "ping_id"])
def test_blank_required_field_rejected(field):
    df = _one()
    df[field] = ""
    r = process(df)
    assert r.audit.iloc[0]["reject_reason"] == gv.R_MISSING_FIELD


def test_missing_column_is_schema_error():
    with pytest.raises(ValueError, match="missing required columns"):
        process(_one().drop(columns=["lat"]))


def test_empty_input_does_not_crash():
    df = _one().iloc[0:0]
    r = process(df)
    assert len(r.audit) == 0 and r.daily.empty


def test_out_of_order_flag_uses_file_order():
    df = mk([{"t": T0 + timedelta(seconds=20), "pos": road_a(3000)},
             {"t": T0 + timedelta(seconds=10), "pos": road_a(3100)},
             {"t": T0 + timedelta(seconds=30), "pos": road_a(3200)}])
    flags = gv.detect_out_of_order(gv.normalise(df))
    assert flags.tolist() == [False, True, False]


def test_threshold_helpers_are_strict():
    assert not gv.is_off_road(100.0) and gv.is_off_road(100.1)
    assert not gv.is_impossible_speed(150.0) and gv.is_impossible_speed(150.1)
    assert gv.implied_speed_kmh(35.0, 139.0, 35.0, 139.0, 0) is None
    assert gv.is_suspicious_jump(130.0) and not gv.is_suspicious_jump(90.0)


# ---------------------------------------------------------------- dataset / integration
def test_dataset_has_at_least_2000_pings_and_is_labelled_synthetic(pings):
    assert len(pings) >= 2000
    assert set(pings["data_origin"]) == {"SYNTHETIC"}
    assert pings["driver_id"].replace("", pd.NA).dropna().nunique() >= 10


def test_manifest_covers_every_seeded_category():
    m = json.loads((__import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "seeded_defects.json").read_text())
    assert m["synthetic"] is True
    for tag in ("DUP_DROPPED", "NOISE_OFFROAD", "SPEED_JUMP", "EDGE_100M_KEEP", "EDGE_100_5M_REJECT",
                "EDGE_150KMH_KEEP", "EDGE_150_5KMH_REJECT", "LATE", "LATE_ACROSS_CUTOFF",
                "INVALID_COORDINATES", "INVALID_TIMESTAMP", "MISSING_FIELD", "GAP_EDGE_30S_END", "GAP_90S_END"):
        assert m["tag_counts"].get(tag, 0) > 0, tag
    assert m["out_of_order_rows_in_file_order"] > 0 and len(m["missing_intervals"]) > 0


def test_every_ping_decision_matches_ground_truth_label(pings, labels, corrected_result):
    """Engine acceptance + reason must equal the generator's ground truth for ALL rows."""
    a = corrected_result.audit.merge(labels, on="ping_id")
    truth_accept = a["truth_accept"] == "True"
    engine_accept = a["status"] == "ACCEPTED"
    assert (truth_accept == engine_accept).all(), a.loc[truth_accept != engine_accept, ["ping_id", "seed_tag", "reject_reason"]].head()
    rej = a[~engine_accept]
    assert (rej["reject_reason"] == rej["truth_reason"]).all()


def test_every_rejection_has_a_reason_and_nothing_is_silently_dropped(pings, corrected_result):
    a = corrected_result.audit
    assert len(a) == len(pings)                                   # one audit row per input row
    assert a.loc[a["status"] == "REJECTED", "reject_reason"].notna().all()
    assert a.loc[a["status"] == "ACCEPTED", "reject_reason"].isna().all()
