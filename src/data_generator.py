"""Deterministic SYNTHETIC GPS ping generator with deliberately seeded defects.

Everything produced here is fictional (drivers, roads, zones, times).  The
generator also writes ground-truth labels: which pings a correct system must
accept/reject and why.  Labels are NEVER read by the calculation engine; they
feed the independent oracle (src/oracle.py) and the tests.

Run:  python -m src.data_generator
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import gps_validator as gv
from .campaign_zone import ZONES
from .geo import (ROADS, haversine_m, point_along_polyline, polyline_length,
                  to_latlon, to_xy)

SEED = 20261116
N_PINGS = 260
PING_INTERVAL_S = 10
UTC = timezone.utc
CUT_UTC = datetime(2026, 3, 15, 0, 0, 0, tzinfo=UTC)      # 00:00 UTC
CUT_JST = datetime(2026, 3, 14, 15, 0, 0, tzinfo=UTC)     # 00:00 UTC+9 (15:00 UTC)
MIDDAY_START = datetime(2026, 3, 14, 6, 0, 0, tzinfo=UTC)

ROAD_ZONE = {"ROAD_A": "ZONE_A", "ROAD_B": "ZONE_B", "ROAD_C": "ZONE_C"}
DRIVER_PLAN = [  # (driver_id, road, group)
    ("D001", "ROAD_A", "UTC_MIDNIGHT"), ("D002", "ROAD_B", "UTC_MIDNIGHT"),
    ("D003", "ROAD_C", "UTC_MIDNIGHT"), ("D004", "ROAD_A", "UTC_MIDNIGHT"),
    ("D005", "ROAD_B", "JST_MIDNIGHT"), ("D006", "ROAD_C", "JST_MIDNIGHT"),
    ("D007", "ROAD_A", "JST_MIDNIGHT"), ("D008", "ROAD_B", "JST_MIDNIGHT"),
    ("D009", "ROAD_C", "MIDDAY"), ("D010", "ROAD_A", "MIDDAY"),
    ("D011", "ROAD_B", "MIDDAY"), ("D012", "ROAD_C", "MIDDAY"),
]

# truth label per tag: (accepted?, reason when rejected)
TAGS = {
    "CLEAN": (True, ""),
    "LATE": (True, ""),
    "LATE_ACROSS_CUTOFF": (True, ""),
    "DUP_KEPT": (True, ""),
    "DUP_DROPPED": (False, gv.R_DUPLICATE),
    "NOISE_OFFROAD": (False, gv.R_OFF_ROAD),
    "EDGE_100M_KEEP": (True, ""),
    "EDGE_100_5M_REJECT": (False, gv.R_OFF_ROAD),
    "SPEED_JUMP": (False, gv.R_IMPOSSIBLE_SPEED),
    "EDGE_150KMH_KEEP": (True, ""),
    "EDGE_150_5KMH_REJECT": (False, gv.R_IMPOSSIBLE_SPEED),
    "GAP_EDGE_30S_END": (True, ""),
    "GAP_90S_END": (True, ""),
    "GAP_300S_END": (True, ""),
    "INVALID_COORDINATES": (False, gv.R_INVALID_COORDINATES),
    "INVALID_TIMESTAMP": (False, gv.R_INVALID_TIMESTAMP),
    "MISSING_FIELD": (False, gv.R_MISSING_FIELD),
}


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rll(x: float, y: float) -> tuple[float, float]:
    lat, lon = to_latlon(x, y)
    return round(lat, 7), round(lon, 7)


class _DriverSim:
    """Simulated true trajectory of one driver along one road (ping-pong laps)."""

    def __init__(self, rng: np.random.Generator, road: str, group: str):
        self.rng, self.road, self.group = rng, road, group
        self.line = ROADS[road]
        self.L = polyline_length(self.line)
        self.zone = ZONES[ROAD_ZONE[road]]
        for _ in range(200):
            self._simulate()
            k = self._first_inside_run(9)
            if k is not None and k + 14 < N_PINGS:
                self.mid = k + 4
                break
        else:  # pragma: no cover
            raise RuntimeError("could not place trajectory in zone")

    def _simulate(self) -> None:
        u = self.rng.uniform(0, 2 * self.L)
        v = 45.0
        self.s, self.dir, self.xy = [], [], []
        for _ in range(N_PINGS):
            m = u % (2 * self.L)
            s, d = (m, 1) if m <= self.L else (2 * self.L - m, -1)
            (px, py), (ux, uy) = point_along_polyline(self.line, s)
            jitter = float(np.clip(self.rng.normal(0, 8), -25, 25))
            nx, ny = -uy * d, ux * d
            self.s.append(s); self.dir.append(d)
            self.xy.append((px + nx * jitter, py + ny * jitter))
            v = float(np.clip(v + self.rng.normal(0, 3), 28, 58))
            u += v / 3.6 * PING_INTERVAL_S

    def _first_inside_run(self, n: int):
        inside = [self.zone.contains_xy(p) for p in self.xy]
        for k in range(len(inside) - n):
            if all(inside[k:k + n]):
                return k
        return None

    def road_xy(self, s: float):
        return point_along_polyline(self.line, s)


def _pick(pool: set[int], rng: np.random.Generator, n: int, radius: int, ok=lambda i: True) -> list[int]:
    chosen: list[int] = []
    for i in rng.permutation(sorted(pool)):
        i = int(i)
        if len(chosen) >= n:
            break
        if i in pool and ok(i):
            chosen.append(i)
            pool.difference_update(range(i - radius, i + radius + 1))
    return chosen


def _generate_driver(driver: str, road: str, group: str, rng: np.random.Generator):
    sim = _DriverSim(rng, road, group)
    mid = sim.mid
    offs = int(rng.choice([3, 4, 6, 7]))
    if group == "UTC_MIDNIGHT":
        t0 = CUT_UTC - timedelta(seconds=mid * PING_INTERVAL_S + offs)
    elif group == "JST_MIDNIGHT":
        t0 = CUT_JST - timedelta(seconds=mid * PING_INTERVAL_S + offs)
    else:
        t0 = MIDDAY_START + timedelta(seconds=int(rng.integers(0, 6 * 3600)))
    ts = [t0 + timedelta(seconds=i * PING_INTERVAL_S) for i in range(N_PINGS)]
    ll = [_rll(*p) for p in sim.xy]

    pool = set(range(3, N_PINGS - 3))
    pool.difference_update(range(mid - 5, mid + 8))   # keep the cutoff straddle clean
    removed: set[int] = set()
    gaps = []

    # --- missing intervals (gaps): 2 missing -> dt 30 s (edge, billed); 9 -> 100 s; 29 -> 300 s
    gap_plan = [(2, "GAP_EDGE_30S_END"), (9, "GAP_90S_END")] + ([(29, "GAP_300S_END")] if int(driver[1:]) % 2 == 0 else [])
    end_tags: dict[int, str] = {}
    for n_missing, tag in gap_plan:
        span = n_missing + 8
        starts = [i for i in sorted(pool) if all(j in pool for j in range(i, i + span))]
        g = int(rng.choice(starts))
        first_missing = g + 4
        removed.update(range(first_missing, first_missing + n_missing))
        end_tags[first_missing + n_missing] = tag
        gaps.append({"driver_id": driver, "tag": tag, "missing_pings": n_missing,
                     "dt_seconds": (n_missing + 1) * PING_INTERVAL_S,
                     "from_ts": _iso(ts[first_missing - 1]), "to_ts": _iso(ts[first_missing + n_missing])})
        pool.difference_update(range(g, g + span))

    seeds: dict[int, dict] = {}

    # --- noise edge cases at exactly 100 m (keep) and 100.5 m (reject) from the road
    def place_offset(i: int, metres: float, sign: int):
        (px, py), (ux, uy) = sim.road_xy(sim.s[i])
        d = sim.dir[i]
        nx, ny = -uy * d, ux * d
        return _rll(px + sign * nx * metres, py + sign * ny * metres)

    def edge_offset_ok(target: float):
        def ok(i: int) -> bool:
            ll_i = place_offset(i, target, 1)
            return round(gv.offroad_distance_m(*ll_i), 1) == round(target, 1)
        return ok

    for tag, target in (("EDGE_100M_KEEP", 100.0), ("EDGE_100_5M_REJECT", 100.5)):
        for i in _pick(pool, rng, 1, 2, edge_offset_ok(target)):
            sign = int(rng.choice([-1, 1]))
            ll_i = place_offset(i, target, sign)
            if round(gv.offroad_distance_m(*ll_i), 1) != round(target, 1):
                ll_i = place_offset(i, target, 1)
            ll[i] = ll_i
            seeds[i] = {"tag": tag, "offroad_m": round(gv.offroad_distance_m(*ll_i), 4)}

    # --- noise > 100 m
    for i in _pick(pool, rng, 5, 2):
        d = float(rng.uniform(120, 600))
        sign = int(rng.choice([-1, 1]))
        ll[i] = place_offset(i, d, sign)
        assert gv.is_off_road(gv.offroad_distance_m(*ll[i]))
        seeds[i] = {"tag": "NOISE_OFFROAD", "offroad_m": round(gv.offroad_distance_m(*ll[i]), 2)}

    # --- impossible speed jumps along the road (on-road, so only the speed rule catches them)
    for i in _pick(pool, rng, 3, 2):
        dist = float(rng.uniform(800, 2000))
        s2 = sim.s[i] + sim.dir[i] * dist
        if not (0 < s2 < sim.L):
            s2 = sim.s[i] - sim.dir[i] * dist
        if not (0 < s2 < sim.L):
            s2 = sim.s[i] + sim.dir[i] * (sim.L - sim.s[i] if sim.dir[i] > 0 else sim.s[i]) * 0.9
        (px, py), _ = sim.road_xy(s2)
        ll[i] = _rll(px, py)
        spd = haversine_m(*ll[i - 1], *ll[i]) / PING_INTERVAL_S * 3.6
        assert spd > 160, spd
        seeds[i] = {"tag": "SPEED_JUMP", "implied_speed_kmh": round(spd, 2)}

    # --- speed edge cases: exactly 150.0 km/h (keep) and 150.5 km/h (reject) vs previous ping
    def speed_target(i: int, kmh: float):
        j = i - 1
        if sim.dir[j] != sim.dir[i] or not (0 < sim.s[j] + sim.dir[j] * 800 < sim.L):
            return None
        target = kmh / 3.6 * PING_INTERVAL_S
        lo, hi = 150.0, 800.0
        f = lambda dlt: haversine_m(*ll[j], *_rll(*sim.road_xy(sim.s[j] + sim.dir[j] * dlt)[0])) - target  # noqa: E731
        if f(lo) > 0 or f(hi) < 0:
            return None
        for _ in range(80):
            mid_d = (lo + hi) / 2
            lo, hi = (mid_d, hi) if f(mid_d) < 0 else (lo, mid_d)
        pos = _rll(*sim.road_xy(sim.s[j] + sim.dir[j] * (lo + hi) / 2)[0])
        spd = haversine_m(*ll[j], *pos) / PING_INTERVAL_S * 3.6
        return pos if round(spd, 1) == round(kmh, 1) else None

    for tag, kmh in (("EDGE_150KMH_KEEP", 150.0), ("EDGE_150_5KMH_REJECT", 150.5)):
        for i in _pick(pool, rng, 1, 2, lambda i, k=kmh: speed_target(i, k) is not None):
            pos = speed_target(i, kmh)
            ll[i] = pos
            spd = haversine_m(*ll[i - 1], *ll[i]) / PING_INTERVAL_S * 3.6
            seeds[i] = {"tag": tag, "implied_speed_kmh": round(spd, 4)}

    # --- late-arriving pings and duplicates
    late_extra: dict[int, float] = {}
    for i in _pick(pool, rng, 8, 1):
        late_extra[i] = float(rng.integers(120, 900))
        seeds[i] = {"tag": "LATE", "ingest_delay_extra_s": late_extra[i]}
    if group in ("UTC_MIDNIGHT", "JST_MIDNIGHT"):
        i = mid + 1  # the ping just after the cutoff arrives 10 minutes late
        late_extra[i] = 600.0
        seeds[i] = {"tag": "LATE_ACROSS_CUTOFF", "ingest_delay_extra_s": 600.0}
    dup_idx = _pick(pool, rng, 6, 2)

    # ---------- assemble rows
    rows: list[dict] = []
    for i in range(N_PINGS):
        if i in removed:
            continue
        base_ingest = ts[i] + timedelta(seconds=float(rng.integers(1, 5)))
        ingest = base_ingest + timedelta(seconds=late_extra.get(i, 0.0))
        tag = seeds.get(i, {}).get("tag", end_tags.get(i, "CLEAN"))
        rows.append({"driver_id": driver, "idx": i, "ts": ts[i], "ingest": ingest,
                     "lat": ll[i][0], "lon": ll[i][1], "tag": tag, "detail": seeds.get(i, {})})
    by_idx = {r["idx"]: r for r in rows}

    dup_rows = []
    for n, i in enumerate(dup_idx):
        orig = by_idx[i]
        exact = bool(rng.random() < 0.5)
        if exact:
            lat, lon = orig["lat"], orig["lon"]
        else:
            ang = float(rng.uniform(0, 2 * math.pi)); r = float(rng.uniform(15, 40))
            x, y = to_xy(orig["lat"], orig["lon"])
            lat, lon = _rll(x + r * math.cos(ang), y + r * math.sin(ang))
        mode = ["orig_first", "dup_first", "tie"][n % 3] if n < 3 else "orig_first"
        delta = 0 if mode == "tie" else int(rng.integers(1, 4))
        dup = {"driver_id": driver, "idx": i, "ts": orig["ts"], "lat": lat, "lon": lon,
               "ingest": orig["ingest"] + (timedelta(seconds=delta) if mode != "dup_first" else -timedelta(seconds=delta)),
               "tag": "DUP_PAIR", "detail": {"exact_copy": exact, "mode": mode}, "_orig": orig}
        dup_rows.append(dup)
    return rows, dup_rows, gaps, {"mid": mid, "t0": _iso(t0), "group": group, "road": road}


def _invalid_rows(rng: np.random.Generator, drivers_ts: list[tuple[str, datetime]]):
    """Eight structurally invalid rows (every one must be rejected with a specific reason)."""
    specs = [
        ("INVALID_COORDINATES", {"lat": "95.3", "lon": "139.76"}),
        ("INVALID_COORDINATES", {"lat": "35.68", "lon": "181.2"}),
        ("INVALID_COORDINATES", {"lat": "abc", "lon": "139.76"}),
        ("INVALID_TIMESTAMP", {"ts_utc": "not-a-time"}),
        ("INVALID_TIMESTAMP", {"ts_utc": "2026-13-45T25:61:00Z"}),
        ("MISSING_FIELD", {"lat": ""}),
        ("MISSING_FIELD", {"driver_id": ""}),
        ("MISSING_FIELD", {"ts_utc": ""}),
    ]
    out = []
    for n, (tag, over) in enumerate(specs):
        driver, t = drivers_ts[int(rng.integers(0, len(drivers_ts)))]
        row = {"driver_id": driver, "ts_utc": _iso(t + timedelta(seconds=5)),
               "ingest_ts_utc": _iso(t + timedelta(seconds=7 + n)), "lat": "35.68", "lon": "139.76"}
        row.update(over)
        out.append((tag, row))
    return out


def generate_dataset(seed: int = SEED):
    """Return ``(pings_df, labels_df, manifest_dict)`` - fully deterministic for a seed."""
    master = np.random.SeedSequence(seed)
    children = master.spawn(len(DRIVER_PLAN) + 1)
    all_rows, all_dups, all_gaps, driver_info = [], [], [], {}
    for (driver, road, group), child in zip(DRIVER_PLAN, children[:-1]):
        rows, dups, gaps, info = _generate_driver(driver, road, group, np.random.default_rng(child))
        all_rows += rows; all_dups += dups; all_gaps += gaps; driver_info[driver] = info
    rng_inv = np.random.default_rng(children[-1])
    sample = [(r["driver_id"], r["ts"]) for r in all_rows[:: max(1, len(all_rows) // 50)]]
    invalid = _invalid_rows(rng_inv, sample)

    # ---- merge everything in ingest order and assign ping ids
    records = []
    for r in all_rows:
        records.append({"driver_id": r["driver_id"], "ts_utc": _iso(r["ts"]), "ingest": r["ingest"],
                        "lat": r["lat"], "lon": r["lon"], "tag": r["tag"], "detail": r["detail"],
                        "_key": (r["driver_id"], r["idx"], 0)})
    for d in all_dups:
        records.append({"driver_id": d["driver_id"], "ts_utc": _iso(d["ts"]), "ingest": d["ingest"],
                        "lat": d["lat"], "lon": d["lon"], "tag": "DUP_PAIR", "detail": d["detail"],
                        "_key": (d["driver_id"], d["idx"], 1), "_orig_key": (d["driver_id"], d["idx"], 0)})
    for tag, row in invalid:
        ing = datetime.strptime(row["ingest_ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        records.append({"driver_id": row["driver_id"], "ts_utc": row["ts_utc"], "ingest": ing,
                        "lat": row["lat"], "lon": row["lon"], "tag": tag, "detail": {},
                        "_key": ("INV", len(records), 0)})
    records.sort(key=lambda r: (r["ingest"], r["_key"]))
    for n, r in enumerate(records, start=1):
        r["ping_id"] = f"P{n:06d}"

    # ---- resolve duplicate pairs: first ingest (then smaller ping_id) is kept
    by_key = {r["_key"]: r for r in records}
    for r in records:
        if r["tag"] == "DUP_PAIR" and "_orig_key" in r:
            o = by_key[r["_orig_key"]]
            kept, dropped = sorted([o, r], key=lambda x: (x["ingest"], x["ping_id"]))
            kept["tag"], dropped["tag"] = "DUP_KEPT", "DUP_DROPPED"
            kept["detail"] = {**kept["detail"], "dup_partner": dropped["ping_id"]}
            dropped["detail"] = {**dropped["detail"], "dup_partner": kept["ping_id"]}

    pings = pd.DataFrame([{
        "ping_id": r["ping_id"], "driver_id": r["driver_id"], "ts_utc": r["ts_utc"],
        "ingest_ts_utc": _iso(r["ingest"]), "lat": r["lat"], "lon": r["lon"],
        "data_origin": "SYNTHETIC",
    } for r in records])
    labels = pd.DataFrame([{
        "ping_id": r["ping_id"], "seed_tag": r["tag"],
        "truth_accept": TAGS[r["tag"]][0], "truth_reason": TAGS[r["tag"]][1],
        "detail": json.dumps(r["detail"], sort_keys=True, default=str),
    } for r in records])

    # ---- manifest
    tag_counts = labels["seed_tag"].value_counts().to_dict()
    ooo = _count_out_of_order(pings)
    manifest = {
        "synthetic": True,
        "notice": "All data is SYNTHETIC. Seeded defects are deliberate; ground truth lives in ping_labels.csv.",
        "seed": seed, "n_pings_per_driver_before_seeding": N_PINGS,
        "report_cutoffs": {"UTC": _iso(CUT_UTC), "UTC+9": _iso(CUT_JST)},
        "total_rows": int(len(pings)),
        "tag_counts": {k: int(v) for k, v in sorted(tag_counts.items())},
        "out_of_order_rows_in_file_order": ooo,
        "late_arriving_rows": int(tag_counts.get("LATE", 0) + tag_counts.get("LATE_ACROSS_CUTOFF", 0)),
        "duplicate_rows_to_collapse": int(tag_counts.get("DUP_DROPPED", 0)),
        "noise_gt_100m_rows": int(tag_counts.get("NOISE_OFFROAD", 0) + tag_counts.get("EDGE_100_5M_REJECT", 0)),
        "impossible_speed_rows": int(tag_counts.get("SPEED_JUMP", 0) + tag_counts.get("EDGE_150_5KMH_REJECT", 0)),
        "invalid_rows": int(sum(tag_counts.get(t, 0) for t in ("INVALID_COORDINATES", "INVALID_TIMESTAMP", "MISSING_FIELD"))),
        "edge_case_rows_kept": int(sum(tag_counts.get(t, 0) for t in ("EDGE_100M_KEEP", "EDGE_150KMH_KEEP"))),
        "missing_intervals": all_gaps,
        "drivers": driver_info,
        "seeded_defects": [
            {"ping_id": r["ping_id"], "driver_id": r["driver_id"], "ts_utc": r["ts_utc"], "tag": r["tag"],
             "expected": ("ACCEPT" if TAGS[r["tag"]][0] else "REJECT:" + TAGS[r["tag"]][1]), "detail": r["detail"]}
            for r in records if r["tag"] != "CLEAN" and not r["tag"].startswith("GAP_")
        ],
    }
    return pings, labels, manifest


def _count_out_of_order(pings: pd.DataFrame) -> int:
    n = 0
    for _, g in pings[pings["ts_utc"].str.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")].groupby("driver_id"):
        ts = pd.to_datetime(g["ts_utc"], errors="coerce", utc=True)
        n += int((ts < ts.cummax().shift(1)).sum())
    return n


def write_dataset(out_dir: Path, seed: int = SEED) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    pings, labels, manifest = generate_dataset(seed)
    pings.to_csv(out_dir / "synthetic_gps_pings.csv", index=False)
    labels.to_csv(out_dir / "ping_labels.csv", index=False)
    (out_dir / "seeded_defects.json").write_text(json.dumps(manifest, indent=2, default=str))
    return manifest


if __name__ == "__main__":
    data_dir = Path(__file__).resolve().parents[1] / "data"
    m = write_dataset(data_dir)
    print(json.dumps({k: v for k, v in m.items() if k not in ("seeded_defects", "drivers", "missing_intervals")}, indent=2))
