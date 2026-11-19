# final-directive.md

> **Synthetic data only.** The baseline is an intentionally defective reference written for this Quest (not employer code). Rates (USD 0.80/km, 40 % driver share) are invented.

## 1. Objective

Make the problem *gone*, not just visible: the distance used to bill advertisers for kilometres driven **inside a campaign zone** must follow the stated rules, and a regression check must prove it (fails on the old behaviour, passes on the fix).

## 2. Scope

**In:** synthetic dataset (>= 2,000 pings) with seeded faults; a deliberately wrong baseline; a QA tool (validator, quality checker, zone validator, distance engine, dashboard); root-cause analysis; corrected engine; before/after per driver and day; regression suite; list of what is still unsolved.
**Out:** real GPS data, real road networks, restating already-issued invoices, production deployment (see section 12).

## 3. Requirements (from the brief)

| # | Rule | Implemented in |
|---|---|---|
| R1 | A ping more than 100 m off the road path is ignored (exactly 100 m is valid) | `gps_validator.is_off_road`, engine step 4 |
| R2 | A ping implying more than 150 km/h is ignored (exactly 150 is valid) | `gps_validator.is_impossible_speed`, engine step 5 |
| R3 | Duplicates (same driver + timestamp) count once, deterministically | `gps_validator.find_duplicates` |
| R4 | Late / out-of-order pings are placed by their own timestamp | engine sorts by (driver, `ts_utc`) |
| R5 | Timestamps are UTC; daily report cut at 00:00 (UTC; UTC+9 variant also supported) | `split_by_report_day`, `EngineConfig.report_utc_offset_hours` |
| R6 | Distance counts only while inside the zone | `campaign_zone.Zone.clip_fraction` |
| R7 | Rejected pings are never silently dropped | audit table, one row per input ping with a reason code |

## 4. Technical architecture

```
CSV pings --> normalise/validate fields --> collapse duplicates --> sort by event time
          --> off-road filter (> 100 m) --> speed filter (> 150 km/h vs last accepted)
          --> segments between accepted pings --> gap rule (> 30 s not billed)
          --> clip chord to each zone --> split in-zone km across report days
          --> (driver, date, zone) km   +   audit log (status, reason, offroad_m, speed, lateness)
```

Independent verification path: generator ground-truth labels --> `src/oracle.py` (shapely, own haversine, `datetime` day split) --> `data/expected_results.csv`. The engine never reads labels or expected results (tested).

## 5. Dataset (synthetic, deterministic, seed 20261116)

2,894 rows, 12 drivers, 3 zones (2 rectangles, 1 pentagon), 3 roads, 2026-03-14/15. Valid 2,694; rejected 200 (72 duplicates, 72 off-road, 48 impossible speed, 8 structurally invalid). 104 valid rows arrive out of order (105 raw; one is a structurally invalid row), 104 arrive late (120-900 s), 24 exact-edge rows (12 at 100.0 m, 12 at 150.0 km/h) that must be kept, 12 x 30 s gaps (billed) and 18 longer gaps (not billed), 46 rows within 60 s of the 00:00 UTC cut and 47 within 60 s of the 00:00 UTC+9 cut, 1,057 accepted pings inside a zone. Schema, tags and assumptions A1-A9: `data/DATA_DICTIONARY.md`; every seeded ping: `data/seeded_defects.json`.

## 6. QA tool

* **Validator / quality checker** (`src/gps_validator.py`): required fields, lat/lon range, timestamp parse, duplicates, out-of-order flag, distance to road, implied speed, suspicious-jump warning (> 120 km/h), reason codes.
* **Zone validator** (`src/campaign_zone.py`): inside/outside (boundary inside), Cyrus-Beck clipping for entry/exit.
* **Engine** (`src/distance_calculator.py`): described in section 4.
* **Defect detector** (`src/defect_detector.py`): anomaly scan; re-introduces each defect one at a time and measures km and USD; collects evidence rows.
* **Dashboard** (`app.py`, Streamlit): totals, valid / rejected / duplicate / noise / speed counts, distance before and after and the difference, per-driver daily table and chart, defect summary with root causes and evidence, rejected-ping audit, map of accepted vs rejected pings, financial impact, 00:00 UTC / UTC+9 switch, CSV upload.
* AI note: the tool contains no AI model; AI was used to build it (`reports/ai_usage_log.md`).

## 7. Defect resolution

All eight defects are fixed in the corrected engine; each has a unit-level regression test and is part of the oracle comparison. Detail and evidence: `reports/defect_report.md`; ranking and rationale: `intent.md`.

| ID | Fix |
|---|---|
| D-ORDER | sort by event time; arrival order is only a tie-break |
| D-SPEED | reject if implied speed vs last accepted ping is > 150.0 km/h |
| D-GAP | do not bill a chord spanning > 30 s |
| D-NOISE | reject if > 100.0 m from the road |
| D-ZONE | clip each chord to the zone; bill only the inside part |
| D-PREC | full coordinate precision (no rounding before measuring) |
| D-DAY | split in-zone km across report days pro rata to time |
| D-DUP | keep earliest-ingest ping per (driver, timestamp); tie -> smaller `ping_id` |

## 8. Completion (acceptance) criteria and status

| Criterion | Status | Evidence |
|---|---|---|
| Regression test fails on the old behaviour | **Met** | `GPS_IMPL=baseline pytest tests/test_regression.py::test_matches_oracle_all_days` -> 2 failed (worst row 42.2 km off) |
| Same test passes on the fix | **Met** | same command with `corrected` -> 2 passed |
| Corrected result equals independent oracle | **Met** | all 16 driver-days / 16 zone rows, both day cuts, max error about 1e-9 km; 3 extra seeds also match |
| No previously valid scenario broke | **Met** | clean-trip tests; all-rules-off equals baseline to 1e-9 km |
| Noise, duplicates, late pings, day cut, zone boundary covered | **Met** | `tests/test_gps_noise.py`, `test_duplicate_pings.py`, `test_timestamp_ordering.py`, `test_utc_boundary.py`, `test_campaign_zone.py` |
| Tests actually bite | **Met** | 9 of 9 injected engine bugs caught (`reports/mutation_check.md`) |

## 9. Test execution results (actual)

`python -m pytest tests -v` -> **127 passed, 0 failed, 0 skipped** (about 50 s). Per-file table, the baseline/corrected outputs and the mutation table: `reports/test_results.md`.

## 10. Before-and-after analysis (00:00 UTC cut, per driver and day)

Total: **434.25 km -> 134.32 km** (difference +299.93 km, +223 %). Advertiser bill USD 347.40 -> 107.46 (**USD 239.94 over-billed**); driver payout error USD 95.98. Negative rows are days where the baseline *under*-counts (e.g. midnight split, exit segments). Reasons are the defects whose individual re-introduction changes that driver-day by at least 0.005 km (full list with km: `reports/before_after_comparison.csv`).

| Driver | Report date (UTC) | Original km | Corrected km | Diff km | Diff % | Main reasons (marginal order) |
|---|---|---|---|---|---|---|
| D001 | 2026-03-14 | 0.601 | 0.718 | -0.117 | -16.3% | D-DAY |
| D001 | 2026-03-15 | 31.822 | 9.632 | 22.191 | +230.4% | D-ORDER, D-SPEED, D-DAY |
| D002 | 2026-03-14 | 0.545 | 0.596 | -0.051 | -8.6% | D-DAY |
| D002 | 2026-03-15 | 30.296 | 10.592 | 19.704 | +186.0% | D-SPEED, D-ORDER, D-GAP |
| D003 | 2026-03-14 | 0.856 | 0.509 | 0.348 | +68.4% | D-ZONE, D-DAY |
| D003 | 2026-03-15 | 54.530 | 16.009 | 38.521 | +240.6% | D-ORDER, D-NOISE, D-ZONE |
| D004 | 2026-03-14 | 0.799 | 0.878 | -0.079 | -9.0% | D-DAY, D-ZONE |
| D004 | 2026-03-15 | 45.803 | 7.644 | 38.159 | +499.2% | D-ORDER, D-SPEED, D-GAP |
| D005 | 2026-03-14 | 28.205 | 11.081 | 17.124 | +154.5% | D-ORDER, D-SPEED, D-ZONE |
| D006 | 2026-03-14 | 52.935 | 10.718 | 42.217 | +393.9% | D-ORDER, D-SPEED, D-GAP |
| D007 | 2026-03-14 | 46.461 | 7.558 | 38.903 | +514.7% | D-ORDER, D-SPEED, D-PREC |
| D008 | 2026-03-14 | 16.352 | 9.406 | 6.946 | +73.8% | D-ORDER, D-GAP, D-SPEED |
| D009 | 2026-03-14 | 20.896 | 12.064 | 8.832 | +73.2% | D-ORDER, D-SPEED, D-NOISE |
| D010 | 2026-03-14 | 29.901 | 9.694 | 20.206 | +208.4% | D-ORDER, D-SPEED, D-GAP |
| D011 | 2026-03-14 | 34.407 | 14.163 | 20.244 | +142.9% | D-ORDER, D-SPEED, D-PREC |
| D012 | 2026-03-14 | 39.846 | 13.062 | 26.784 | +205.1% | D-ORDER, D-SPEED, D-GAP |

Worked example (D007, 2026-03-14): baseline 46.461 km vs corrected 7.558 km (+38.9 km). Re-introducing only arrival-order processing adds 14.2 km, only the speed-filter removal 3.8 km, and so on (marginal, they overlap). Reproduce: `python -m src.reporting`, then open `reports/before_after_comparison.csv`.

## 11. Regression results

See section 8 and `reports/test_results.md`. Reproduce:

```bash
GPS_IMPL=baseline  python -m pytest tests/test_regression.py::test_matches_oracle_all_days -q   # expected: 2 failed
GPS_IMPL=corrected python -m pytest tests/test_regression.py::test_matches_oracle_all_days -q   # expected: 2 passed
```

## 12. Known limitations / what is still NOT solved

1. Synthetic data and synthetic roads only; no map matching, no real GPS error model (errors are uniform offsets and jumps).
2. **First-ping poisoning**: speed is checked against the last accepted ping, so a bad first ping of a trip would make the following good pings look too fast. Not seeded or handled.
3. Gap rule (> 30 s not billed) is an assumption; the business may prefer interpolation or a longer tolerance.
4. A ping that arrives days late changes an already-issued day; no restatement/credit workflow exists.
5. Zones must be convex; roads are polylines.
6. Impacts are marginal; interacting defects do not add up (stated in the reports).
7. Day cut: the brief says UTC, the quest page says UTC+9. Both work; confirm which one production uses.
8. Performance is untested beyond about 3,000 rows (Python loop).
9. The dashboard was smoke-tested by script and a live health check; it was not reviewed visually by a human.
10. Effort: this repository was produced by an AI agent in one session; the candidate's own hours are to be entered in `reports/ai_usage_log.md`.

## 13. Handoff instructions

```bash
unzip quest-gps-qa.zip && cd quest-gps-qa
python -m pip install -r requirements.txt
python -m pytest tests -q          # 127 passed
streamlit run app.py               # dashboard on http://localhost:8501
```
Change a rule: `src/gps_validator.py` (thresholds) or `src/distance_calculator.py` (`EngineConfig`). After any change run the tests and `python tools_mutation_check.py`. To test against another dataset: `python -c "from src.data_generator import write_dataset; from pathlib import Path; write_dataset(Path('data_alt'), seed=123)"` then `python -m src.oracle`-style comparison as in `tests/test_regression.py::test_engine_matches_oracle_on_other_seeds`.

## 14. Links (appendix: results and handoff)

Paths are relative to the submitted repository archive. **Hosted links: [ADD PRIVATE REVIEWER-ACCESSIBLE LINK TO THE REPOSITORY OR ZIP]. [ADD LOOM LINK].** These two links did not exist when this file was written; open both from a signed-out window before submitting.

| Item | Location |
|---|---|
| Tool (engine, validator, zones) | `src/` , dashboard `app.py` |
| Faulty baseline | `baseline/faulty_calculator.py` |
| Fix (corrected engine) | `src/distance_calculator.py` |
| Dataset | `data/synthetic_gps_pings.csv`, `data/ping_labels.csv`, `data/seeded_defects.json`, `data/DATA_DICTIONARY.md` |
| Independent expected results | `data/expected_results.csv`, `src/oracle.py` |
| Regression and unit tests | `tests/` (start with `tests/test_regression.py`) |
| Test results | `reports/test_results.md`, `reports/mutation_check.md` |
| Defect report | `reports/defect_report.md` |
| Before/after | `reports/before_after_comparison.csv` |
| AI usage | `reports/ai_usage_log.md` |
| Intent | `intent.md` |
| Loom script | `reports/loom_script.md` |
