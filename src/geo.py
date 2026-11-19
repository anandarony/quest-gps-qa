"""Geospatial helpers for the synthetic GPS QA project.

SYNTHETIC DATA NOTICE: roads and campaign zones defined here are fictional.

Two metrics are used on purpose:
* ``haversine_m``   - great-circle distance between two pings (billing + speed).
* planar (local tangent plane) metres - used for point-to-road offsets and
  zone clipping.  Over the ~15 km scope of this project the planar error is
  well below 0.1 %, and it keeps clipping fractions exact and cheap.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

ORIGIN_LAT = 35.6800
ORIGIN_LON = 139.7600
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(ORIGIN_LAT))
EARTH_RADIUS_M = 6_371_008.8

Point = tuple[float, float]


def to_xy(lat: float, lon: float) -> Point:
    """Project WGS84 degrees to local planar metres (x east, y north)."""
    return ((lon - ORIGIN_LON) * M_PER_DEG_LON, (lat - ORIGIN_LAT) * M_PER_DEG_LAT)


def to_latlon(x: float, y: float) -> Point:
    """Inverse of :func:`to_xy`; returns ``(lat, lon)``."""
    return (ORIGIN_LAT + y / M_PER_DEG_LAT, ORIGIN_LON + x / M_PER_DEG_LON)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def haversine_m_np(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Vectorised haversine in metres."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (
        np.sin((p2 - p1) / 2) ** 2
        + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * np.arcsin(np.minimum(1.0, np.sqrt(a)))


# --------------------------------------------------------------------------
# Synthetic road network (planar metres from the origin).  FICTIONAL.
# --------------------------------------------------------------------------
ROADS: dict[str, list[Point]] = {
    "ROAD_A": [(-1000.0, 0.0), (5000.0, 600.0), (11000.0, 300.0)],
    "ROAD_B": [(0.0, 4000.0), (4000.0, 5200.0), (9000.0, 5000.0)],
    "ROAD_C": [(0.0, 9000.0), (6000.0, 9500.0), (12000.0, 11000.0)],
}


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    """Distance (planar metres) from point ``p`` to segment ``ab``."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0.0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / denom))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def distance_to_polyline(p: Point, line: Sequence[Point]) -> float:
    """Minimum distance (planar metres) from ``p`` to a polyline."""
    return min(point_segment_distance(p, line[i], line[i + 1]) for i in range(len(line) - 1))


def distance_to_road_network(lat: float, lon: float, roads: dict[str, list[Point]] | None = None) -> float:
    """Distance in metres from a WGS84 point to the nearest road."""
    roads = ROADS if roads is None else roads
    p = to_xy(lat, lon)
    return min(distance_to_polyline(p, line) for line in roads.values())


def polyline_length(line: Sequence[Point]) -> float:
    return sum(math.dist(line[i], line[i + 1]) for i in range(len(line) - 1))


def point_along_polyline(line: Sequence[Point], s: float) -> tuple[Point, Point]:
    """Position and unit tangent after travelling ``s`` metres along ``line``.

    ``s`` is clamped to ``[0, length]``.
    """
    s = max(0.0, min(s, polyline_length(line)))
    for i in range(len(line) - 1):
        seg = math.dist(line[i], line[i + 1])
        if s <= seg or i == len(line) - 2:
            t = 0.0 if seg == 0 else s / seg
            ux, uy = (line[i + 1][0] - line[i][0]) / seg, (line[i + 1][1] - line[i][1]) / seg
            return (line[i][0] + t * (line[i + 1][0] - line[i][0]),
                    line[i][1] + t * (line[i + 1][1] - line[i][1])), (ux, uy)
        s -= seg
    raise AssertionError("unreachable")
