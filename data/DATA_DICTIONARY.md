# Data dictionary and schema (ALL DATA IS SYNTHETIC)

Generated deterministically by `python -m src.data_generator` (seed `20261116`). Nothing here comes from a real driver, vehicle, advertiser or employer.

## Files

| File | Purpose |
|---|---|
| `synthetic_gps_pings.csv` | Input the pipeline sees (2,894 rows). Seeded defects are mixed in; nothing in this file says which rows are defective. |
| `ping_labels.csv` | Ground truth per `ping_id`: seed tag and whether a correct system must accept it. Read ONLY by the oracle and tests, never by the engine. |
| `seeded_defects.json` | Manifest: counts per defect type, every seeded ping, missing-interval list, per-driver setup. |
| `expected_results.csv` | Independent oracle output (km per driver / report date / zone) for two daily cut-offs. |

## `synthetic_gps_pings.csv` schema

| Column | Type | Description |
|---|---|---|
| `ping_id` | string `Pnnnnnn` | Assigned in arrival (ingest) order, so file order = arrival order. |
| `driver_id` | string `D001`..`D012` | Synthetic driver. Blank in one seeded invalid row. |
| `ts_utc` | ISO-8601 UTC `YYYY-MM-DDTHH:MM:SSZ` | Event time (when the phone sampled the GPS). Deliberately invalid/blank in 3 seeded rows. |
| `ingest_ts_utc` | ISO-8601 UTC | Arrival time at the server. `ingest - ts` is the lateness. |
| `lat`, `lon` | decimal degrees (7 dp, about 1 cm) | WGS84. Strings so that invalid values (`abc`, `95.3`) survive loading. |
| `data_origin` | constant `SYNTHETIC` | Explicit label. |

## `ping_labels.csv`

| Column | Description |
|---|---|
| `ping_id` | Join key. |
| `seed_tag` | One of the tags below. |
| `truth_accept` | `True` if a correct system must accept the ping. |
| `truth_reason` | Required rejection reason code when `truth_accept` is `False`. |
| `detail` | JSON with the seeding detail (offset in metres, implied speed, partner ping, ...). |

### Seed tags (row counts in the committed dataset)

| Tag | Rows | Meaning | Expected |
|---|---|---|---|
| `CLEAN` | 2,464 | Normal on-road ping (up to 8 m lateral jitter) | accept |
| `LATE` | 96 | Arrives 120-900 s after its event time; also out of file order | accept, placed by own `ts_utc` |
| `LATE_ACROSS_CUTOFF` | 8 | Ping just after the daily cut arrives 10 minutes late | accept, counted on its own day |
| `DUP_KEPT` / `DUP_DROPPED` | 72 / 72 | Same driver + same `ts_utc`; copy is exact or 15-40 m off. First arrival wins; an ingest tie is broken by the smaller `ping_id` | keep / `DUPLICATE` |
| `NOISE_OFFROAD` | 60 | 120-600 m from the road | `OFF_ROAD_GT_100M` |
| `EDGE_100M_KEEP` | 12 | Exactly 100.0 m from the road | accept (rule says *more than* 100 m) |
| `EDGE_100_5M_REJECT` | 12 | 100.5 m from the road | `OFF_ROAD_GT_100M` |
| `SPEED_JUMP` | 36 | On-road ping 0.8-2 km away from the previous ping (about 290-720 km/h) | `IMPOSSIBLE_SPEED_GT_150KMH` |
| `EDGE_150KMH_KEEP` | 12 | Exactly 150.0 km/h from the previous ping | accept (rule says *above* 150) |
| `EDGE_150_5KMH_REJECT` | 12 | 150.5 km/h | `IMPOSSIBLE_SPEED_GT_150KMH` |
| `GAP_EDGE_30S_END` | 12 | First ping after a 30 s gap (2 pings missing) | accept; the 30 s segment IS billed |
| `GAP_90S_END` | 12 | First ping after a 100 s gap | accept; the gap segment is NOT billed |
| `GAP_300S_END` | 6 | First ping after a 300 s gap | accept; the gap segment is NOT billed |
| `INVALID_COORDINATES` | 3 | lat 95.3, lon 181.2, lat `abc` | `INVALID_COORDINATES` |
| `INVALID_TIMESTAMP` | 2 | `not-a-time`, `2026-13-45T25:61:00Z` | `INVALID_TIMESTAMP` |
| `MISSING_FIELD` | 3 | blank lat / driver / timestamp | `MISSING_FIELD` |

## Synthetic world

* **Roads** (`src/geo.py`): `ROAD_A/B/C`, three-vertex polylines, 10-12 km long, in planar metres around (35.68 N, 139.76 E).
* **Zones** (`src/campaign_zone.py`): `ZONE_A`, `ZONE_B` (rectangles) and `ZONE_C` (convex pentagon), each crossing one road. Boundary counts as inside.
* **Drivers**: 12; four per road. Each drives about 43 minutes (260 pings at 10 s) ping-ponging along its road at 28-58 km/h.
* **Day cut groups**: D001-D004 are inside a zone at 00:00 UTC (2026-03-15); D005-D008 are inside a zone at 00:00 UTC+9 (2026-03-14T15:00Z); D009-D012 are mid-day. Timestamps are offset by 3-7 s so a 10 s segment straddles the cut.
* **Coordinate precision**: 7 decimals (about 1 cm). Threshold comparisons are done at 0.1 m / 0.1 km/h (assumption A5), otherwise "exactly 100 m" could not be reproduced.

## Expected results (`expected_results.csv`)

Columns: `driver_id, report_date, zone_id, expected_km, report_utc_offset_hours`. Rows for offset `0.0` (00:00 UTC, the brief) and `9.0` (00:00 UTC+9, the quest page text). Built by `src/oracle.py` from ground-truth labels with shapely and `datetime` arithmetic (no shared calculation code with the engine).

## Assumptions

| ID | Assumption |
|---|---|
| A1 | Billable distance = great-circle chord between consecutive ACCEPTED pings. |
| A2 | Only the part of a chord inside a zone is billed; the boundary counts as inside. |
| A3 | A chord spanning more than 30 s (more than 3 ping intervals) is not billed: the path and zone membership are unknown. 30 s exactly is billed. |
| A4 | A chord's in-zone part is shared across report days pro rata to time (constant speed within 10 s). A ping exactly at the cut belongs to the new day. |
| A5 | Thresholds compare at 0.1 m / 0.1 km/h; limits are strict ("more than 100 m", "above 150 km/h"). |
| A6 | Duplicate winner = earliest `ingest_ts_utc`, then smallest `ping_id`. |
| A7 | Speed is measured against the last ACCEPTED ping of the same driver, using straight-line distance and the real elapsed time. |
| A8 | The brief says the cut is 00:00 UTC; the quest page says 00:00 local (UTC+9). Both are supported (`report_utc_offset_hours`); default is UTC and both are tested against the oracle. |
| A9 | Advertiser rate USD 0.80/km and driver payout share 40 % are invented for this exercise. |
