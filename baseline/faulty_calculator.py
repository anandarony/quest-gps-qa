"""INTENTIONALLY DEFECTIVE BASELINE - FICTIONAL REFERENCE CALCULATION.

This is NOT any employer's production code.  It was written for this
assessment to reproduce the classic mistakes of a naive per-ping distance job.
Do not use it for anything except regression comparison.

Seeded implementation defects (IDs used throughout the reports):
  D-ORDER  processes pings in ARRIVAL (file) order instead of event-time order
  D-DUP    no (driver, timestamp) de-duplication
  D-NOISE  no filter for pings > 100 m off the road path
  D-SPEED  no filter for implied speed > 150 km/h
  D-ZONE   bills the WHOLE segment when only its END ping lies in a zone
  D-DAY    attributes a whole segment to the report day of its END ping
  D-GAP    bills straight-line distance across missing-ping gaps
  D-PREC   rounds coordinates to 4 decimals (~11 m) before measuring
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.campaign_zone import ZONES  # noqa: E402  (static geometry only)
from src.geo import haversine_m  # noqa: E402

COORD_DECIMALS = 4  # D-PREC


def calculate_daily_distance(pings: pd.DataFrame, report_utc_offset_hours: float = 0.0) -> pd.DataFrame:
    """Return DataFrame[driver_id, report_date, zone_id, km] using the faulty logic."""
    df = pings.copy()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["ts"] = pd.to_datetime(df["ts_utc"], errors="coerce", utc=True, format="ISO8601")
    df = df.dropna(subset=["driver_id", "ts", "lat", "lon"])
    df = df[(df["driver_id"].astype(str).str.strip() != "") & (df["lat"].abs() <= 90) & (df["lon"].abs() <= 180)]
    df["lat"] = df["lat"].round(COORD_DECIMALS)   # D-PREC
    df["lon"] = df["lon"].round(COORD_DECIMALS)

    totals: dict[tuple[str, str, str], float] = {}
    # D-ORDER: groupby keeps file (arrival) order; no sort by timestamp.
    for driver, g in df.groupby("driver_id", sort=False):
        prev = None
        for row in g.itertuples(index=False):
            if prev is not None:
                # D-DUP / D-NOISE / D-SPEED / D-GAP: no checks of any kind here
                metres = haversine_m(prev.lat, prev.lon, row.lat, row.lon)
                for zid, zone in ZONES.items():
                    if zone.contains(row.lat, row.lon):          # D-ZONE: END ping decides
                        day = (row.ts + pd.Timedelta(hours=report_utc_offset_hours)).strftime("%Y-%m-%d")  # D-DAY
                        key = (driver, day, zid)
                        totals[key] = totals.get(key, 0.0) + metres / 1000.0
            prev = row
    out = pd.DataFrame([{"driver_id": k[0], "report_date": k[1], "zone_id": k[2], "km": v}
                        for k, v in sorted(totals.items())],
                       columns=["driver_id", "report_date", "zone_id", "km"])
    return out
