"""Campaign zones: point-in-polygon and segment clipping (SYNTHETIC zones).

Zones are convex polygons in planar metres.  The boundary counts as INSIDE.
Clipping uses the Cyrus-Beck algorithm so that a segment which enters or
leaves a zone contributes only the portion that lies inside.
"""
from __future__ import annotations

from dataclasses import dataclass

from .geo import Point, to_xy

EPS = 1e-12


@dataclass(frozen=True)
class Zone:
    zone_id: str
    vertices: tuple[Point, ...]  # counter-clockwise, convex

    def contains_xy(self, p: Point) -> bool:
        """Boundary-inclusive point-in-convex-polygon test."""
        n = len(self.vertices)
        for i in range(n):
            x1, y1 = self.vertices[i]
            x2, y2 = self.vertices[(i + 1) % n]
            cross = (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1)
            if cross < -1e-9:
                return False
        return True

    def contains(self, lat: float, lon: float) -> bool:
        return self.contains_xy(to_xy(lat, lon))

    def clip_fraction(self, p0: Point, p1: Point) -> tuple[float, float] | None:
        """Return ``(t_in, t_out)`` of segment p0->p1 inside the zone, else None."""
        t0, t1 = 0.0, 1.0
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        n = len(self.vertices)
        for i in range(n):
            x1, y1 = self.vertices[i]
            x2, y2 = self.vertices[(i + 1) % n]
            # inside half-plane: cross((edge), (p - v1)) >= 0
            ex, ey = x2 - x1, y2 - y1
            num = ex * (p0[1] - y1) - ey * (p0[0] - x1)   # value at t=0
            den = ex * dy - ey * dx                        # slope w.r.t. t
            if abs(den) < EPS:
                if num < -1e-9:
                    return None  # parallel and outside
                continue
            t = -num / den
            if den > 0:      # entering
                t0 = max(t0, t)
            else:            # leaving
                t1 = min(t1, t)
            if t0 > t1:
                return None
        return (t0, t1)


ZONES: dict[str, Zone] = {
    "ZONE_A": Zone("ZONE_A", ((3000.0, -500.0), (6500.0, -500.0), (6500.0, 1500.0), (3000.0, 1500.0))),
    "ZONE_B": Zone("ZONE_B", ((2500.0, 4000.0), (6000.0, 4000.0), (6000.0, 6500.0), (2500.0, 6500.0))),
    "ZONE_C": Zone("ZONE_C", ((5000.0, 8500.0), (9000.0, 8700.0), (10000.0, 10500.0),
                              (7000.0, 11000.0), (4500.0, 10000.0))),
}


def zones_containing(lat: float, lon: float, zones: dict[str, Zone] | None = None) -> list[str]:
    zones = ZONES if zones is None else zones
    p = to_xy(lat, lon)
    return [zid for zid, z in zones.items() if z.contains_xy(p)]


def zone_overlap_fractions(p0: Point, p1: Point, zones: dict[str, Zone] | None = None) -> dict[str, tuple[float, float]]:
    """Per-zone ``(t_in, t_out)`` fractions of the chord p0->p1 inside each zone."""
    zones = ZONES if zones is None else zones
    out: dict[str, tuple[float, float]] = {}
    for zid, z in zones.items():
        c = z.clip_fraction(p0, p1)
        if c is not None and c[1] > c[0]:
            out[zid] = c
    return out
