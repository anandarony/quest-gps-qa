"""GPS data validator (A) and GPS quality checker (B).

All thresholds follow the business rules:
* ping more than 100 m from the road path -> rejected (exactly 100 m is kept)
* implied speed above 150 km/h           -> rejected (exactly 150 km/h is kept)
* same driver + same timestamp           -> collapsed to one ping
Threshold comparisons use values rounded to 0.1 (m or km/h): coordinates are
stored with 7 decimals (~1 cm), so a ping seeded at "exactly 100 m" is only
reproducible to about +/-1 cm.  This precision rule is an explicit assumption.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from .geo import distance_to_road_network, haversine_m

REQUIRED_COLUMNS = ["ping_id", "driver_id", "ts_utc", "lat", "lon"]
OPTIONAL_COLUMNS = ["ingest_ts_utc"]

# Rejection reason codes (stable identifiers used in audit logs and tests)
R_MISSING_FIELD = "MISSING_FIELD"
R_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
R_INVALID_COORDINATES = "INVALID_COORDINATES"
R_DUPLICATE = "DUPLICATE"
R_OFF_ROAD = "OFF_ROAD_GT_100M"
R_IMPOSSIBLE_SPEED = "IMPOSSIBLE_SPEED_GT_150KMH"

MAX_OFFSET_M = 100.0
MAX_SPEED_KMH = 150.0
SUSPICIOUS_SPEED_KMH = 120.0  # soft warning level for "suspicious jump"
THRESHOLD_PRECISION = 1  # decimals used when comparing against thresholds


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Return a typed copy with ``row_order``, ``ts``, ``ingest_ts`` and numeric lat/lon.

    Missing required columns raise ``ValueError`` (a schema failure, not a row failure).
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    out = df.copy()
    out["row_order"] = np.arange(len(out))
    out["ts"] = pd.to_datetime(out["ts_utc"], errors="coerce", utc=True, format="ISO8601")
    if "ingest_ts_utc" in out.columns:
        out["ingest_ts"] = pd.to_datetime(out["ingest_ts_utc"], errors="coerce", utc=True, format="ISO8601")
    else:
        out["ingest_ts"] = pd.NaT
    out["ingest_ts"] = out["ingest_ts"].fillna(out["ts"])
    out["lat_f"] = pd.to_numeric(out["lat"], errors="coerce")
    out["lon_f"] = pd.to_numeric(out["lon"], errors="coerce")
    return out


def validate_fields(df: pd.DataFrame) -> pd.Series:
    """Per-row field-level rejection reason (``None`` when the row is structurally valid).

    ``df`` must already be passed through :func:`normalise`.
    """
    reason = pd.Series([None] * len(df), index=df.index, dtype=object)
    blank = lambda s: s.isna() | (s.astype(str).str.strip() == "")  # noqa: E731
    missing = blank(df["driver_id"]) | blank(df["ping_id"]) | blank(df["ts_utc"]) | blank(df["lat"]) | blank(df["lon"])
    reason[missing] = R_MISSING_FIELD
    bad_ts = reason.isna() & df["ts"].isna()
    reason[bad_ts] = R_INVALID_TIMESTAMP
    bad_coord = reason.isna() & (
        df["lat_f"].isna() | df["lon_f"].isna()
        | ~np.isfinite(df["lat_f"]) | ~np.isfinite(df["lon_f"])
        | (df["lat_f"].abs() > 90) | (df["lon_f"].abs() > 180)
    )
    reason[bad_coord] = R_INVALID_COORDINATES
    return reason


def find_duplicates(df: pd.DataFrame) -> pd.Series:
    """Boolean Series: True for every ping that must be collapsed into an earlier one.

    Deterministic rule: for each (driver_id, ts) keep the ping with the earliest
    ``ingest_ts`` and, on ties, the smallest ``ping_id`` (string compare, then row order).
    Only rows with a parsable timestamp participate.
    """
    cand = df[df["ts"].notna() & df["driver_id"].notna()].copy()
    cand["_pid"] = cand["ping_id"].astype(str)
    cand = cand.sort_values(["driver_id", "ts", "ingest_ts", "_pid", "row_order"], kind="mergesort")
    dup = cand.duplicated(subset=["driver_id", "ts"], keep="first")
    out = pd.Series(False, index=df.index)
    out.loc[dup[dup].index] = True
    return out


def detect_out_of_order(df: pd.DataFrame) -> pd.Series:
    """True where a ping arrives (file order) after a ping of the same driver with a later timestamp."""
    flag = pd.Series(False, index=df.index)
    for _, g in df[df["ts"].notna()].sort_values("row_order").groupby("driver_id", sort=False):
        running_max = g["ts"].cummax().shift(1)
        flag.loc[g.index] = (g["ts"] < running_max).fillna(False).values
    return flag


def offroad_distance_m(lat: float, lon: float) -> float:
    """Distance from the synthetic road network in metres."""
    return distance_to_road_network(lat, lon)


def is_off_road(distance_m: float, max_offset_m: float = MAX_OFFSET_M) -> bool:
    """True when strictly more than ``max_offset_m`` (compared at 0.1 m precision)."""
    return round(distance_m, THRESHOLD_PRECISION) > max_offset_m


def implied_speed_kmh(lat0: float, lon0: float, lat1: float, lon1: float, dt_seconds: float) -> Optional[float]:
    """Straight-line implied speed in km/h; ``None`` if ``dt_seconds`` is zero."""
    if dt_seconds == 0:
        return None
    return haversine_m(lat0, lon0, lat1, lon1) / abs(dt_seconds) * 3.6


def is_impossible_speed(speed_kmh: Optional[float], max_speed_kmh: float = MAX_SPEED_KMH) -> bool:
    """True when strictly above ``max_speed_kmh`` (compared at 0.1 km/h precision)."""
    return speed_kmh is not None and round(speed_kmh, THRESHOLD_PRECISION) > max_speed_kmh


def is_suspicious_jump(speed_kmh: Optional[float]) -> bool:
    """Soft warning: implied speed above 120 km/h (may still be accepted)."""
    return speed_kmh is not None and speed_kmh > SUSPICIOUS_SPEED_KMH


def validate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Convenience wrapper returning ``normalise`` output plus ``field_reason``,
    ``is_duplicate`` and ``out_of_order_arrival`` columns."""
    out = normalise(df)
    out["field_reason"] = validate_fields(out)
    out["is_duplicate"] = find_duplicates(out[out["field_reason"].isna()]).reindex(out.index, fill_value=False)
    out["out_of_order_arrival"] = detect_out_of_order(out[out["field_reason"].isna()]).reindex(out.index, fill_value=False)
    return out
