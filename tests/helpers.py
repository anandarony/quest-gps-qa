"""Helpers to build tiny hand-made ping tables for unit tests (SYNTHETIC)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from src.geo import to_latlon

UTC = timezone.utc
T0 = datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ll(x: float, y: float) -> tuple[float, float]:
    """Planar metres -> (lat, lon) rounded like the dataset (7 decimals)."""
    lat, lon = to_latlon(x, y)
    return round(lat, 7), round(lon, 7)


def road_a(x: float) -> tuple[float, float]:
    """Point exactly ON synthetic ROAD_A (first leg: y = 0.1 * (x + 1000)), valid for x in [-1000, 5000]."""
    return ll(x, 0.1 * (x + 1000.0))


def road_c(x: float) -> tuple[float, float]:
    """Point exactly ON ROAD_C first leg ((0,9000)->(6000,9500))."""
    return ll(x, 9000.0 + x * 500.0 / 6000.0)


def mk(rows: list[dict]) -> pd.DataFrame:
    """Build a raw ping table. Each row: driver, t (datetime or str), pos=(lat,lon),
    optional ingest (datetime), pid."""
    out = []
    for n, r in enumerate(rows, 1):
        t = r["t"]
        ing = r.get("ingest", t if isinstance(t, datetime) else None)
        out.append({
            "ping_id": r.get("pid", f"T{n:04d}"),
            "driver_id": r.get("driver", "D1"),
            "ts_utc": iso(t) if isinstance(t, datetime) else t,
            "ingest_ts_utc": iso(ing) if isinstance(ing, datetime) else (ing or ""),
            "lat": r["pos"][0], "lon": r["pos"][1],
        })
    return pd.DataFrame(out)


def straight_trip(xs: list[float], start: datetime = T0, step_s: int = 10, driver: str = "D1", road=road_a) -> pd.DataFrame:
    """Pings every ``step_s`` seconds at road x-positions ``xs`` (on ROAD_A by default)."""
    return mk([{"driver": driver, "t": start + timedelta(seconds=i * step_s), "pos": road(x)} for i, x in enumerate(xs)])


def road_a2(x: float) -> tuple[float, float]:
    """Point ON ROAD_A second leg ((5000,600)->(11000,300): y = 600 - 0.05 * (x - 5000)), x in [5000, 11000]."""
    return ll(x, 600.0 - 0.05 * (x - 5000.0))


def offset_from_road_a(x: float, metres: float) -> tuple[float, float]:
    """Point exactly ``metres`` (planar, perpendicular) away from ROAD_A first leg at road-x ``x``."""
    import math
    n = math.hypot(1.0, 0.1)
    return ll(x - 0.1 * metres / n, 0.1 * (x + 1000.0) + metres / n)


def x_for_chord(x1: float, metres: float) -> float:
    """Road-A x of the point whose great-circle chord from road_a(x1) is ``metres`` (bisection)."""
    from src.geo import haversine_m
    lo, hi = x1, x1 + 2.0 * metres
    for _ in range(80):
        mid = (lo + hi) / 2
        if haversine_m(*road_a(x1), *road_a(mid)) < metres:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
