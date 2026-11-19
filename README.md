# quest-gps-qa: find the problem, build the tool, fix it

> **Everything here is synthetic.** Drivers, roads, zones, rates and timestamps are invented. `baseline/faulty_calculator.py` is an *intentionally defective* reference written for this assessment; it is **not** any employer's production code.

**Scenario.** A fictional vehicle-advertising platform records a GPS ping every 10 s. Advertisers pay per km driven *inside a campaign zone*. Pings more than 100 m off the road or implying more than 150 km/h are noise; duplicates count once; late/out-of-order pings go by their own timestamp; timestamps are UTC; the daily report is cut at 00:00.

**Headline result (seeded dataset, 2,894 pings, 12 drivers, 3 zones, 2026-03-14/15):**

| | km in zones | advertiser bill at USD 0.80/km |
|---|---|---|
| Defective baseline | 434.25 | USD 347.40 |
| Corrected engine | 134.32 | USD 107.46 |
| Difference | +299.93 km (+223 %) | **USD 239.94 over-billed** |

The corrected engine equals an independent oracle for every driver/day/zone (max error about 1e-9 km). The same regression test fails against the baseline (2 of 2) and passes against the corrected engine (2 of 2).

## Install and run

```bash
cd quest-gps-qa
python -m pip install -r requirements.txt          # Python 3.10+; tested on 3.13
python -m src.data_generator                       # regenerate data/*.csv|json (deterministic, seed 20261116)
python -m src.oracle                               # regenerate data/expected_results.csv (independent oracle)
python -m src.reporting                            # regenerate reports/before_after_comparison.csv + defect_report.md
python -m pytest tests -q                          # 127 tests
GPS_IMPL=baseline python -m pytest tests/test_regression.py::test_matches_oracle_all_days -q   # FAILS (2 failed) - proves the check bites
GPS_IMPL=corrected python -m pytest tests/test_regression.py::test_matches_oracle_all_days -q  # passes
python tools_mutation_check.py                     # injects 9 bugs into the engine; every one must be caught
streamlit run app.py                               # QA dashboard (sidebar: choose 00:00 UTC or 00:00 UTC+9 cut)
```

## Layout

| Path | What |
|---|---|
| `src/gps_validator.py` | A. validator and B. quality checker (fields, duplicates, out-of-order, > 100 m, > 150 km/h, jumps) |
| `src/campaign_zone.py` | C. zone polygons, point-in-polygon, Cyrus-Beck segment clipping (entry/exit) |
| `src/distance_calculator.py` | D. corrected engine: dedupe, event-time ordering, filters, gap rule, zone clipping, report-day split, audit log |
| `src/defect_detector.py` | measures every defect by ablation; anomaly scan; evidence |
| `src/reporting.py` | before/after table, financials, defect report generator |
| `src/oracle.py` | **independent** expected results (ground-truth labels + shapely) |
| `src/data_generator.py` | deterministic synthetic data with seeded defects |
| `baseline/faulty_calculator.py` | the deliberately wrong reference calculation (8 labelled defects) |
| `tests/` | pytest suite (unit, integration, boundary, negative, regression, meta, dashboard smoke) |
| `app.py` | E. Streamlit QA dashboard |
| `data/` | dataset, labels, manifest, expected results, `DATA_DICTIONARY.md` |
| `reports/` | defect report, before/after CSV, test results, mutation check, AI log, progress and final execution reports, Loom script |
| `intent.md`, `final-directive.md` | the two written deliverables |

## Assumptions to know first

* The brief says the daily cut is 00:00 **UTC**; the Match Hire page text says 00:00 **UTC+9**. Both are implemented (`EngineConfig.report_utc_offset_hours`), default UTC, and both are tested against the oracle.
* Gaps longer than 30 s are not billed (nothing is interpolated); exactly 30 s is billed.
* Limits are strict: exactly 100 m and exactly 150 km/h are valid; comparisons use 0.1 m / 0.1 km/h because coordinates are stored to about 1 cm.
* Full list: `data/DATA_DICTIONARY.md` (A1-A9).

## AI usage

An AI agent (Claude) wrote the code, data, tests and docs; no AI runs inside the tool. See `reports/ai_usage_log.md` for what it produced, what it got wrong, and the candidate-review checklist that the human must complete.
