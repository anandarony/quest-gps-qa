"""INDEPENDENT expected-results oracle.

Deliberately shares no calculation code with ``distance_calculator``:
* acceptance decisions come from the generator's ground-truth labels
  (``data/ping_labels.csv``), not from the engine's filters;
* in-zone length uses shapely polygon intersection (engine: Cyrus-Beck clipping);
* haversine uses the atan2 formulation (engine: asin);
* report-day splitting uses ``datetime`` arithmetic with explicit tz offsets
  (engine: epoch floor-division).
Only static geometry constants (origin, zone vertices) are imported.

Business assumptions encoded here (same as the engine, documented in README):
 A1 distance between consecutive ACCEPTED pings is the great-circle chord;
 A2 a chord is billed only for the part inside a zone, boundary inclusive;
 A3 a chord whose time gap exceeds 30 s is not billed (cannot verify the path);
 A4 the in-zone part is shared across report days pro rata to time.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from shapely.geometry import LineString, Point, Polygon

from .campaign_zone import ZONES
from .geo import M_PER_DEG_LAT, M_PER_DEG_LON, ORIGIN_LAT, ORIGIN_LON

MAX_GAP_S = 30.0
R = 6_371_008.8
_POLYS = {zid: Polygon(z.vertices) for zid, z in ZONES.items()}


def _hav(lat1, lon1, lat2, lon2) -> float:
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df, dl = f2 - f1, math.radians(lon2 - lon1)
    a = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _xy(lat, lon):
    return ((lon - ORIGIN_LON) * M_PER_DEG_LON, (lat - ORIGIN_LAT) * M_PER_DEG_LAT)


def _split_days(t_a: datetime, t_b: datetime, km: float, offset_h: float) -> dict:
    tz = timezone(timedelta(hours=offset_h))
    la, lb = t_a.astimezone(tz), t_b.astimezone(tz)
    if lb <= la:
        return {lb.date().isoformat(): km}
    total = (lb - la).total_seconds()
    out: dict[str, float] = {}
    day = la.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < lb:
        nxt = day + timedelta(days=1)
        lo, hi = max(la, day), min(lb, nxt)
        if hi > lo:
            out[day.date().isoformat()] = out.get(day.date().isoformat(), 0.0) + km * (hi - lo).total_seconds() / total
        day = nxt
    return out


def expected_results(pings: pd.DataFrame, labels: pd.DataFrame, offset_h: float = 0.0) -> pd.DataFrame:
    """Expected km per (driver, report_date, zone) from ground-truth accepted pings."""
    df = pings.merge(labels[["ping_id", "truth_accept"]], on="ping_id")
    df = df[df["truth_accept"].astype(str).str.lower() == "true"].copy()
    df["t"] = pd.to_datetime(df["ts_utc"], utc=True, format="ISO8601")
    acc: dict[tuple, float] = {}
    for driver, g in df.sort_values(["driver_id", "t"]).groupby("driver_id"):
        recs = list(g[["t", "lat", "lon"]].itertuples(index=False))
        for a, b in zip(recs, recs[1:]):
            dt = (b.t - a.t).total_seconds()
            if dt > MAX_GAP_S:
                continue
            chord = _hav(a.lat, a.lon, b.lat, b.lon)
            p0, p1 = _xy(a.lat, a.lon), _xy(b.lat, b.lon)
            line = LineString([p0, p1])
            if line.length == 0:
                continue
            for zid, poly in _POLYS.items():
                inter = line.intersection(poly.buffer(1e-7))
                if inter.is_empty or inter.length == 0:
                    continue
                coords = [c for c in (inter.coords if inter.geom_type == "LineString" else
                                      [pt for g2 in inter.geoms for pt in g2.coords])]
                ts_ = sorted(line.project(Point(c)) / line.length for c in coords)
                t_in, t_out = max(0.0, ts_[0]), min(1.0, ts_[-1])
                km = chord * (t_out - t_in) / 1000.0
                start = b.t.to_pydatetime() if dt == 0 else a.t.to_pydatetime() + timedelta(seconds=t_in * dt)
                end = a.t.to_pydatetime() + timedelta(seconds=t_out * dt)
                for day, k in _split_days(start, end, km, offset_h).items():
                    acc[(driver, day, zid)] = acc.get((driver, day, zid), 0.0) + k
    out = pd.DataFrame([{"driver_id": k[0], "report_date": k[1], "zone_id": k[2], "expected_km": v}
                        for k, v in sorted(acc.items())])
    out["report_utc_offset_hours"] = offset_h
    return out


def write_expected(data_dir: Path) -> pd.DataFrame:
    pings = pd.read_csv(data_dir / "synthetic_gps_pings.csv", dtype=str)
    pings["lat"] = pd.to_numeric(pings["lat"], errors="coerce")
    pings["lon"] = pd.to_numeric(pings["lon"], errors="coerce")
    labels = pd.read_csv(data_dir / "ping_labels.csv", dtype=str)
    res = pd.concat([expected_results(pings, labels, 0.0), expected_results(pings, labels, 9.0)], ignore_index=True)
    res["expected_km"] = res["expected_km"].round(9)
    res.to_csv(data_dir / "expected_results.csv", index=False)
    return res


if __name__ == "__main__":
    d = Path(__file__).resolve().parents[1] / "data"
    r = write_expected(d)
    print(r.groupby("report_utc_offset_hours")["expected_km"].agg(["count", "sum"]))
