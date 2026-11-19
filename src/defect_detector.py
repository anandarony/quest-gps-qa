"""Defect detector: scans data anomalies and MEASURES the impact of each defect.

Method (ablation): the corrected engine is re-run with exactly one business rule
disabled; the difference to the corrected result is that defect's impact.  The
combination of ALL rules disabled reproduces the faulty baseline exactly (this is
asserted in tests/test_regression.py), so the ablation is a faithful attribution
of the baseline's error, not a guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from .distance_calculator import EngineConfig, driver_day_totals, process, split_by_report_day
from .geo import haversine_m
from . import gps_validator as gv

ADVERTISER_RATE_USD_PER_KM = 0.80   # SYNTHETIC, documented assumption
DRIVER_PAYOUT_SHARE = 0.40          # SYNTHETIC: driver earns 40 % of billed amount

# defect id -> (title, engine override that re-introduces the defect, root cause text)
DEFECTS: dict[str, dict] = {
    "D-ORDER": dict(
        title="Late / out-of-order pings processed in arrival order",
        override=dict(ordering="arrival"),
        root_cause=("The job iterates the file in arrival order (`groupby('driver_id', sort=False)` then "
                    "`itertuples()`) and never sorts by `ts_utc`. A ping that arrives 2-15 minutes late is "
                    "measured against the wrong neighbours: the path jumps back to its true position and forward "
                    "again, so chord lengths of 1+ km are billed for 10-second segments."),
        rule="Late and out-of-order pings must be placed according to their own timestamps."),
    "D-SPEED": dict(
        title="Impossible-speed pings (> 150 km/h) are billed",
        override=dict(speed_filter=False),
        root_cause=("No speed check exists. A teleporting ping that sits ON the road (so an off-road filter "
                    "alone would not catch it) adds a long out-and-back chord of 0.8-2 km."),
        rule="Pings implying a speed above 150 km/h must be ignored."),
    "D-GAP": dict(
        title="Distance billed across missing-ping gaps",
        override=dict(gap_rule=False),
        root_cause=("Consecutive rows are joined regardless of elapsed time. After a 100 s or 300 s outage the "
                    "straight line between the last and next ping is billed although the path (and whether "
                    "the vehicle stayed in the zone) is unknown."),
        rule="Missing intervals must not be silently interpolated into billable distance (assumption A3)."),
    "D-NOISE": dict(
        title="GPS noise > 100 m off the road is billed",
        override=dict(offroad_filter=False),
        root_cause=("No distance-to-road check. A ping 120-600 m off the road adds a detour of up to ~2x the "
                    "offset; pings at 100.0 m must stay valid and 100.5 m must be dropped (strict `>`)."),
        rule="GPS pings more than 100 metres off the road path must be ignored."),
    "D-ZONE": dict(
        title="Zone entry/exit billed with the END ping only",
        override=dict(zone_mode="endpoint"),
        root_cause=("`zone.contains(row.lat, row.lon)` decides for the whole segment. The entry segment is "
                    "over-billed (outside part counted), the exit segment is under-billed (inside part dropped)."),
        rule="Distance counts only while the vehicle is inside the zone."),
    "D-DUP": dict(
        title="Duplicate pings (same driver + timestamp) are double counted",
        override=dict(dedupe=False),
        root_cause=("No de-duplication on (driver_id, ts_utc). Duplicates whose coordinates differ by 15-40 m "
                    "create a zig-zag between the copies."),
        rule="Duplicate pings for the same driver at the same timestamp must be collapsed."),
    "D-DAY": dict(
        title="Midnight-straddling segment assigned wholly to the END ping's day",
        override=dict(day_mode="end"),
        root_cause=("The report date comes from the end ping only. A segment that straddles the daily cut is not "
                    "split, so kilometres move between report days (net total unchanged, daily bills wrong)."),
        rule="Daily report cutoff; all timestamps in UTC."),
    "D-PREC": dict(
        title="Coordinates rounded to 4 decimals before measuring",
        override=dict(coord_decimals=4),
        root_cause=("`df['lat'].round(4)` snaps pings to an ~11 m grid, perturbing every 10-second chord."),
        rule="Distance calculation and GPS coordinate precision."),
}


def severity_from_share(abs_share: float) -> tuple[str, str]:
    """Severity/priority from absolute error as a share of the correct total."""
    if abs_share >= 0.10:
        return "Critical", "P0"
    if abs_share >= 0.03:
        return "High", "P1"
    if abs_share >= 0.002:
        return "Medium", "P2"
    return "Low", "P3"


@dataclass
class DefectFinding:
    defect_id: str
    title: str
    rule: str
    root_cause: str
    severity: str
    priority: str
    records_affected: int
    net_km: float
    abs_driver_day_km: float
    advertiser_impact_usd: float
    driver_impact_usd: float
    affected_drivers: list
    evidence: list = field(default_factory=list)
    reproduced: bool = True


def anomaly_scan(pings: pd.DataFrame, cfg: EngineConfig | None = None) -> dict:
    """Data-level counters used by the dashboard and reports."""
    cfg = cfg or EngineConfig()
    res = process(pings, cfg)
    a = res.audit
    reasons = a["reject_reason"].value_counts().to_dict()
    seg = res.segments
    return {
        "total_records": int(len(a)),
        "valid_records": int((a["status"] == "ACCEPTED").sum()),
        "rejected_records": int((a["status"] == "REJECTED").sum()),
        "duplicate_records": int(reasons.get(gv.R_DUPLICATE, 0)),
        "gps_noise_records": int(reasons.get(gv.R_OFF_ROAD, 0)),
        "impossible_speed_records": int(reasons.get(gv.R_IMPOSSIBLE_SPEED, 0)),
        "invalid_field_records": int(sum(reasons.get(r, 0) for r in
                                         (gv.R_MISSING_FIELD, gv.R_INVALID_TIMESTAMP, gv.R_INVALID_COORDINATES))),
        "out_of_order_arrivals": int(a["arrived_out_of_order"].sum()),
        "late_arriving_records": int((a["ingest_delay_s"] > 60).sum()),
        "missing_interval_segments": int(seg["gap_not_billed"].sum()) if len(seg) else 0,
        "reject_reasons": {k: int(v) for k, v in reasons.items()},
    }


def _driver_day(res_daily: pd.DataFrame) -> pd.Series:
    t = driver_day_totals(res_daily)
    return t.set_index(["driver_id", "report_date"])["km"] if len(t) else pd.Series(dtype=float)


def ablation_deltas(pings: pd.DataFrame, cfg: EngineConfig | None = None) -> dict[str, pd.Series]:
    """For each defect id: Series[(driver, date)] = km(defect re-introduced) - km(corrected)."""
    cfg = cfg or EngineConfig()
    correct = _driver_day(process(pings, cfg).daily)
    out = {}
    for did, spec in DEFECTS.items():
        broken = _driver_day(process(pings, replace(cfg, **spec["override"])).daily)
        idx = correct.index.union(broken.index)
        out[did] = (broken.reindex(idx, fill_value=0.0) - correct.reindex(idx, fill_value=0.0))
    return out


def _evidence(did: str, pings: pd.DataFrame, cfg: EngineConfig, corrected) -> list[str]:
    a = corrected.audit
    ev: list[str] = []
    if did == "D-ORDER":
        s = process(pings, replace(cfg, ordering="arrival")).segments
        s = s[s["dt_s"] < 0].sort_values("chord_m", ascending=False).head(3)
        for r in s.itertuples():
            ev.append(f"{r.driver_id}: {r.from_ping}->{r.to_ping} arrives {abs(r.dt_s):.0f}s 'back in time'; "
                      f"straight-line jump of {r.chord_m:,.0f} m between two pings that are 10 s apart in real time")
    elif did == "D-SPEED":
        for r in a[a["reject_reason"] == gv.R_IMPOSSIBLE_SPEED].sort_values("implied_speed_kmh", ascending=False).head(3).itertuples():
            ev.append(f"{r.driver_id} {r.ping_id}: implied {r.implied_speed_kmh:,.0f} km/h")
    elif did == "D-GAP":
        s = corrected.segments
        for r in s[s["gap_not_billed"]].sort_values("chord_m", ascending=False).head(3).itertuples():
            ev.append(f"{r.driver_id}: {r.from_ping}->{r.to_ping} gap {r.dt_s:.0f}s, straight line {r.chord_m:,.0f} m")
    elif did == "D-NOISE":
        for r in a[a["reject_reason"] == gv.R_OFF_ROAD].sort_values("offroad_m", ascending=False).head(3).itertuples():
            ev.append(f"{r.driver_id} {r.ping_id}: {r.offroad_m:.0f} m from the road")
    elif did == "D-ZONE":
        end = process(pings, replace(cfg, zone_mode="endpoint")).segments
        m = corrected.segments.merge(end, on=["driver_id", "from_ping", "to_ping"], suffixes=("_ok", "_end"))
        m["diff"] = m["in_zone_m_end"] - m["in_zone_m_ok"]
        for r in m.reindex(m["diff"].abs().sort_values(ascending=False).index).head(3).itertuples():
            ev.append(f"{r.driver_id}: {r.from_ping}->{r.to_ping} correct in-zone {r.in_zone_m_ok:.1f} m, "
                      f"END-ping rule bills {r.in_zone_m_end:.1f} m")
    elif did == "D-DUP":
        for r in a[a["reject_reason"] == gv.R_DUPLICATE].head(3).itertuples():
            ev.append(f"{r.driver_id} {r.ping_id} duplicates an earlier ping at {r.ts_utc}")
    elif did == "D-DAY":
        s = corrected.segments
        for r in s.itertuples():
            if r.in_zone_m > 0 and r.dt_s > 0 and (r.ts_from.timestamp() + cfg.report_utc_offset_hours * 3600) // 86400 != \
               (r.ts_to.timestamp() + cfg.report_utc_offset_hours * 3600) // 86400:
                pieces = split_by_report_day(r.ts_from.timestamp(), r.ts_to.timestamp(), r.in_zone_m / 1000, cfg.report_utc_offset_hours)
                ev.append(f"{r.driver_id}: {r.from_ping}->{r.to_ping} {r.ts_from:%H:%M:%S}->{r.ts_to:%H:%M:%S}Z "
                          f"split " + ", ".join(f"{d}: {k * 1000:.1f} m" for d, k in pieces) + f"; legacy: all on {pieces[-1][0]}")
                if len(ev) >= 3:
                    break
    elif did == "D-PREC":
        ok = corrected.accepted
        rl = ok["lat_f"].round(4); rn = ok["lon_f"].round(4)
        d = [haversine_m(a1, b1, a2, b2) for a1, b1, a2, b2 in zip(ok["lat_f"], ok["lon_f"], rl, rn)]
        ev.append(f"max coordinate displacement from rounding: {max(d):.1f} m; mean {sum(d) / len(d):.1f} m")
        rounded = process(pings, replace(cfg, coord_decimals=4)).audit.set_index("ping_id")["status"]
        base = corrected.audit.set_index("ping_id")["status"]
        flips = int((base != rounded.reindex(base.index)).sum())
        ev.append(f"rounding flips the accept/reject decision of {flips} pings (edge cases at exactly 100 m / 150 km/h are the most fragile)")
    return ev


def records_affected(did: str, pings: pd.DataFrame, cfg: EngineConfig, corrected) -> int:
    a = corrected.audit
    if did == "D-ORDER":
        return int((a["arrived_out_of_order"] & (a["status"] == "ACCEPTED")).sum())
    if did == "D-SPEED":
        return int((a["reject_reason"] == gv.R_IMPOSSIBLE_SPEED).sum())
    if did == "D-NOISE":
        return int((a["reject_reason"] == gv.R_OFF_ROAD).sum())
    if did == "D-DUP":
        return int((a["reject_reason"] == gv.R_DUPLICATE).sum())
    if did == "D-GAP":
        return int(corrected.segments["gap_not_billed"].sum())
    if did == "D-ZONE":
        end = process(pings, replace(cfg, zone_mode="endpoint")).segments
        m = corrected.segments.merge(end, on=["driver_id", "from_ping", "to_ping"], suffixes=("_ok", "_end"))
        return int(((m["in_zone_m_ok"] - m["in_zone_m_end"]).abs() > 1.0).sum())
    if did == "D-DAY":
        s = corrected.segments
        off = cfg.report_utc_offset_hours * 3600
        return int(sum(1 for r in s.itertuples()
                       if r.in_zone_m > 0 and (r.ts_from.timestamp() + off) // 86400 != (r.ts_to.timestamp() + off) // 86400))
    if did == "D-PREC":
        return int((a["status"] == "ACCEPTED").sum())
    return 0


def detect_defects(pings: pd.DataFrame, cfg: EngineConfig | None = None) -> list[DefectFinding]:
    """Run all ablations and return findings ranked by absolute advertiser exposure."""
    cfg = cfg or EngineConfig()
    corrected = process(pings, cfg)
    total = float(corrected.daily["km"].sum()) or 1.0
    deltas = ablation_deltas(pings, cfg)
    out: list[DefectFinding] = []
    for did, spec in DEFECTS.items():
        d = deltas[did]
        net, absum = float(d.sum()), float(d.abs().sum())
        sev, pri = severity_from_share(absum / total)
        drivers = sorted({k[0] for k, v in d.items() if abs(v) >= 0.005})
        out.append(DefectFinding(
            defect_id=did, title=spec["title"], rule=spec["rule"], root_cause=spec["root_cause"],
            severity=sev, priority=pri, records_affected=records_affected(did, pings, cfg, corrected),
            net_km=net, abs_driver_day_km=absum,
            advertiser_impact_usd=net * ADVERTISER_RATE_USD_PER_KM,
            driver_impact_usd=net * ADVERTISER_RATE_USD_PER_KM * DRIVER_PAYOUT_SHARE,
            affected_drivers=drivers, evidence=_evidence(did, pings, cfg, corrected)))
    out.sort(key=lambda f: f.abs_driver_day_km, reverse=True)
    return out
