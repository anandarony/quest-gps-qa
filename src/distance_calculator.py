"""Corrected distance-calculation engine (D).

Pipeline
--------
1. ``normalise`` + field validation          -> MISSING_FIELD / INVALID_*
2. deterministic duplicate collapse           -> DUPLICATE
3. sort by driver + event timestamp (late / out-of-order pings placed by own ts)
4. road-offset filter (> 100 m)               -> OFF_ROAD_GT_100M
5. impossible-speed filter (> 150 km/h vs last accepted ping) -> IMPOSSIBLE_SPEED_GT_150KMH
6. build segments between consecutive accepted pings
   * gap rule: segments longer than ``max_gap_s`` seconds are NOT billed (assumption A3)
   * zone clipping: only the part of the chord inside a zone is billed
   * UTC-day (or UTC+offset) split: in-zone portion is divided over report days
     in proportion to time (constant speed within a 10 s segment)
7. aggregate to (driver, report_date, zone) kilometres.

The ``EngineConfig`` rule switches exist ONLY so that the defect detector can
measure the marginal impact of each rule (ablation).  Production behaviour is
the default configuration.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import pandas as pd

from . import gps_validator as gv
from .campaign_zone import ZONES, Zone, zone_overlap_fractions, zones_containing
from .geo import haversine_m, to_xy


@dataclass(frozen=True)
class EngineConfig:
    max_offset_m: float = gv.MAX_OFFSET_M
    max_speed_kmh: float = gv.MAX_SPEED_KMH
    max_gap_s: float = 30.0
    report_utc_offset_hours: float = 0.0   # daily cut at 00:00 at this UTC offset (0 = UTC)
    # --- ablation switches (defaults == corrected behaviour) ---
    dedupe: bool = True
    ordering: str = "event"        # "event" (correct) | "arrival" (legacy defect)
    offroad_filter: bool = True
    speed_filter: bool = True
    gap_rule: bool = True
    zone_mode: str = "clip"        # "clip" (correct) | "endpoint" (legacy defect)
    day_mode: str = "split"        # "split" (correct) | "end" (legacy defect)
    coord_decimals: Optional[int] = None  # None = full precision (correct)


@dataclass
class ProcessResult:
    audit: pd.DataFrame      # one row per input ping with status + reason
    accepted: pd.DataFrame
    segments: pd.DataFrame   # one row per consecutive accepted-ping pair
    daily: pd.DataFrame      # driver_id, report_date, zone_id, km


def _day_index(epoch_s: float, offset_h: float) -> int:
    return math.floor((epoch_s + offset_h * 3600.0) / 86400.0)


def _date_str(day_index: int) -> str:
    return (pd.Timestamp("1970-01-01") + pd.Timedelta(days=day_index)).strftime("%Y-%m-%d")


def split_by_report_day(start_s: float, end_s: float, km: float, offset_h: float) -> list[tuple[str, float]]:
    """Divide ``km`` travelled in ``[start_s, end_s]`` over report days.

    Days are half-open ``[cut, next_cut)`` where cut = 00:00 at UTC+offset.
    The kilometres are shared in proportion to the time spent in each day.
    A zero-length interval is attributed to its own day.
    """
    if end_s <= start_s:
        return [(_date_str(_day_index(end_s, offset_h)), km)]
    first, last = _day_index(start_s, offset_h), _day_index(end_s, offset_h)
    if last == first or end_s == start_s:
        return [(_date_str(first), km)]
    pieces: list[tuple[str, float]] = []
    span = end_s - start_s
    for k in range(first, last + 1):
        lo = max(start_s, k * 86400.0 - offset_h * 3600.0)
        hi = min(end_s, (k + 1) * 86400.0 - offset_h * 3600.0)
        if hi > lo:
            pieces.append((_date_str(k), km * (hi - lo) / span))
    return pieces


def _audit_frame(df: pd.DataFrame) -> pd.DataFrame:
    a = pd.DataFrame({
        "ping_id": df["ping_id"].values,
        "driver_id": df["driver_id"].values,
        "ts_utc": df["ts_utc"].values,
        "row_order": df["row_order"].values,
        "status": "ACCEPTED",
        "reject_reason": None,
        "offroad_m": np.nan,
        "implied_speed_kmh": np.nan,
        "ingest_delay_s": ((df["ingest_ts"] - df["ts"]).dt.total_seconds()).values,
        "arrived_out_of_order": df["out_of_order_arrival"].values,
        "suspicious_jump": False,
    })
    a.index = df.index
    return a


def process(pings: pd.DataFrame, cfg: EngineConfig | None = None,
            zones: dict[str, Zone] | None = None) -> ProcessResult:
    """Run the full corrected pipeline on a raw ping table."""
    cfg = cfg or EngineConfig()
    zones = ZONES if zones is None else zones
    df = gv.validate_dataframe(pings)
    audit = _audit_frame(df)

    def reject(mask: pd.Series, reason: str) -> None:
        m = mask & (audit["status"] == "ACCEPTED")
        audit.loc[m, "status"] = "REJECTED"
        audit.loc[m, "reject_reason"] = reason

    # 1. field validation
    for reason in (gv.R_MISSING_FIELD, gv.R_INVALID_TIMESTAMP, gv.R_INVALID_COORDINATES):
        reject(df["field_reason"] == reason, reason)
    # 2. duplicates
    if cfg.dedupe:
        reject(df["is_duplicate"], gv.R_DUPLICATE)

    # 3. ordering
    ok_idx = audit.index[audit["status"] == "ACCEPTED"]
    work = df.loc[ok_idx].copy()
    if cfg.coord_decimals is not None:
        work["lat_f"] = work["lat_f"].round(cfg.coord_decimals)
        work["lon_f"] = work["lon_f"].round(cfg.coord_decimals)
    sort_cols = ["driver_id", "ts", "ping_id", "row_order"] if cfg.ordering == "event" else ["driver_id", "row_order"]
    work["_pid"] = work["ping_id"].astype(str)
    if cfg.ordering == "event":
        work = work.sort_values(["driver_id", "ts", "ingest_ts", "_pid", "row_order"], kind="mergesort")
    else:
        work = work.sort_values(["driver_id", "row_order"], kind="mergesort")

    # 4/5. quality filters (sequential per driver)
    accepted_idx: list = []
    for driver, g in work.groupby("driver_id", sort=False):
        last = None
        for idx, r in g.iterrows():
            off = gv.offroad_distance_m(r["lat_f"], r["lon_f"])
            audit.at[idx, "offroad_m"] = round(off, 3)
            if cfg.offroad_filter and gv.is_off_road(off, cfg.max_offset_m):
                audit.at[idx, "status"] = "REJECTED"
                audit.at[idx, "reject_reason"] = gv.R_OFF_ROAD
                continue
            if last is not None:
                dt = (r["ts"] - last["ts"]).total_seconds()
                spd = gv.implied_speed_kmh(last["lat_f"], last["lon_f"], r["lat_f"], r["lon_f"], dt)
                if spd is not None:
                    audit.at[idx, "implied_speed_kmh"] = round(spd, 3)
                    audit.at[idx, "suspicious_jump"] = gv.is_suspicious_jump(spd)
                    if cfg.speed_filter and gv.is_impossible_speed(spd, cfg.max_speed_kmh):
                        audit.at[idx, "status"] = "REJECTED"
                        audit.at[idx, "reject_reason"] = gv.R_IMPOSSIBLE_SPEED
                        continue
            accepted_idx.append(idx)
            last = r
    accepted = work.loc[accepted_idx]

    # 6. segments
    seg_rows: list[dict] = []
    daily_rows: list[dict] = []
    for driver, g in accepted.groupby("driver_id", sort=False):
        recs = g[["ping_id", "ts", "lat_f", "lon_f"]].to_dict("records")
        for a, b in zip(recs[:-1], recs[1:]):
            dt = (b["ts"] - a["ts"]).total_seconds()
            hav = haversine_m(a["lat_f"], a["lon_f"], b["lat_f"], b["lon_f"])
            gap = cfg.gap_rule and dt > cfg.max_gap_s
            row = {"driver_id": driver, "from_ping": a["ping_id"], "to_ping": b["ping_id"],
                   "ts_from": a["ts"], "ts_to": b["ts"], "dt_s": dt, "chord_m": hav,
                   "gap_not_billed": bool(gap), "in_zone_m": 0.0}
            if not gap:
                p0, p1 = to_xy(a["lat_f"], a["lon_f"]), to_xy(b["lat_f"], b["lon_f"])
                s0, s1 = a["ts"].timestamp(), b["ts"].timestamp()
                if cfg.zone_mode == "clip":
                    for zid, (t_in, t_out) in zone_overlap_fractions(p0, p1, zones).items():
                        km = hav * (t_out - t_in) / 1000.0
                        row["in_zone_m"] += km * 1000.0
                        if cfg.day_mode == "split" and dt > 0:
                            pieces = split_by_report_day(s0 + t_in * dt, s0 + t_out * dt, km, cfg.report_utc_offset_hours)
                        else:
                            pieces = [(_date_str(_day_index(s1, cfg.report_utc_offset_hours)), km)]
                        for day, k in pieces:
                            daily_rows.append({"driver_id": driver, "report_date": day, "zone_id": zid, "km": k})
                else:  # legacy endpoint rule: whole chord counted if END ping is in a zone
                    for zid in zones_containing(b["lat_f"], b["lon_f"], zones):
                        km = hav / 1000.0
                        row["in_zone_m"] += hav
                        if cfg.day_mode == "split" and dt > 0:
                            pieces = split_by_report_day(s0, s1, km, cfg.report_utc_offset_hours)
                        else:
                            pieces = [(_date_str(_day_index(s1, cfg.report_utc_offset_hours)), km)]
                        for day, k in pieces:
                            daily_rows.append({"driver_id": driver, "report_date": day, "zone_id": zid, "km": k})
            seg_rows.append(row)

    segments = pd.DataFrame(seg_rows)
    daily = pd.DataFrame(daily_rows, columns=["driver_id", "report_date", "zone_id", "km"])
    if not daily.empty:
        daily = daily.groupby(["driver_id", "report_date", "zone_id"], as_index=False)["km"].sum()
        daily = daily.sort_values(["driver_id", "report_date", "zone_id"]).reset_index(drop=True)
    audit = audit.drop(columns=[], errors="ignore")
    return ProcessResult(audit=audit, accepted=accepted, segments=segments, daily=daily)


def daily_distance(pings: pd.DataFrame, cfg: EngineConfig | None = None) -> pd.DataFrame:
    """Shortcut: per driver / report-date / zone kilometres."""
    return process(pings, cfg).daily


def driver_day_totals(daily: pd.DataFrame) -> pd.DataFrame:
    """Collapse zones: one row per (driver_id, report_date) with total km."""
    if daily.empty:
        return pd.DataFrame(columns=["driver_id", "report_date", "km"])
    return daily.groupby(["driver_id", "report_date"], as_index=False)["km"].sum()
