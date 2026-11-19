# Test execution results (actual output; generated 2026-11-16 03:24Z)

Command: `python -m pytest tests -v` -> **127 passed in 52.35s**

| Total | Passed | Failed | Skipped | XFail |
|---|---|---|---|---|
| 127 | 127 | 0 | 0 | 0 |

## Per file

| File | Passed | Failed |
|---|---|---|
| tests/test_app_smoke.py | 2 | 0 |
| tests/test_campaign_zone.py | 19 | 0 |
| tests/test_detector_and_reporting.py | 3 | 0 |
| tests/test_duplicate_pings.py | 9 | 0 |
| tests/test_gps_noise.py | 14 | 0 |
| tests/test_gps_validation.py | 25 | 0 |
| tests/test_missing_intervals.py | 9 | 0 |
| tests/test_regression.py | 14 | 0 |
| tests/test_speed_validation.py | 13 | 0 |
| tests/test_timestamp_ordering.py | 9 | 0 |
| tests/test_utc_boundary.py | 10 | 0 |

## Acceptance criterion 1 + 2: the same regression test on both implementations

`GPS_IMPL=baseline python -m pytest tests/test_regression.py::test_matches_oracle_all_days -q`

```
    D002  2026-03-15  ZONE_B  30.840367    11.187896   19.652472
E         2      D003  2026-03-15  ZONE_C  55.386542    16.517957   38.868585
E         3      D004  2026-03-15  ZONE_A  46.602153     8.522660   38.079493
E         4      D005  2026-03-14  ZONE_B   0.518158     0.573569    0.055412
E       assert False
E        +  where False =    driver_id report_date zone_id         km  expected_km  abs_err_km\n0       D001  2026-03-15  ZONE_A  32.422646    10...-03-14  ZONE_B  34.406690    14.162636   20.244054\n15      D012  2026-03-14  ZONE_C  39.846270    13.061848   26.784422.empty

tests/test_regression.py:51: AssertionError
=========================== short test summary info ============================
FAILED tests/test_regression.py::test_matches_oracle_all_days[0.0] - Assertio...
FAILED tests/test_regression.py::test_matches_oracle_all_days[9.0] - Assertio...
2 failed in 0.19s
```

`GPS_IMPL=corrected python -m pytest tests/test_regression.py::test_matches_oracle_all_days -q`

```
..                                                                       [100%]
2 passed in 0.96s
```

The test is `test_matches_oracle_all_days[offset]` for the 00:00 UTC cut (0.0) and the 00:00 UTC+9 cut (9.0). It compares every (driver, report date, zone) with `data/expected_results.csv` at 1e-6 km. Two meta-tests (`test_meta_regression_test_FAILS_on_baseline`, `test_meta_regression_test_PASSES_on_corrected`) run this very command in a subprocess on every full run, so the fail-before / pass-after property is itself under test.

## Acceptance criterion 3: independent oracle comparison

* `test_matches_oracle_all_days[0.0|9.0]`, `test_dataset_daily_totals_match_oracle_across_cutoffs[0.0|9.0]`, `test_dataset_zone_totals_match_oracle_per_zone`: all pass; maximum absolute difference observed about 1e-9 km.
* `test_every_ping_decision_matches_ground_truth_label`: all 2,894 accept/reject decisions and reasons equal the generator's ground truth.
* `test_engine_matches_oracle_on_other_seeds[1|7|20260101]`: pass (not overfitted to one dataset).

## Acceptance criterion 4: nothing previously valid broke

`test_previously_valid_scenario_is_unchanged`, `test_clean_driver_days_unchanged_by_the_fix`, and the zero-defect unit scenarios in each file pass. `test_ablation_of_all_rules_reproduces_baseline_exactly` proves the corrected engine with every new rule switched off equals the old baseline (max difference < 1e-9 km).

## Test power (mutation check)

`python tools_mutation_check.py` injects 9 single-line bugs into the corrected engine; each must make the suite fail:

| Mutant | Result | Failing tests | A non-dataset test that failed |
|---|---|---|---|
| off-road uses >= (exactly 100 m wrongly rejected) | KILLED | 18 | test_offset_boundary[100.0-False] |
| speed uses >= (exactly 150 km/h wrongly rejected) | KILLED | 18 | test_threshold_helpers_are_strict |
| gap rule off-by-one (30 s edge not billed) | KILLED | 14 | test_gap_boundary_30_seconds[30-True] |
| midnight segment not split | KILLED | 13 | test_straddling_segment_is_split_pro_rata_by_time |
| arrival-order processing | KILLED | 25 | test_shuffling_rows_does_not_change_duplicate_decisions |
| duplicates not collapsed | KILLED | 22 | test_same_driver_same_timestamp_is_collapsed |
| zone END-ping rule | KILLED | 17 | test_entry_segment_bills_only_inside_part |
| wrong default day cut | KILLED | 8 | test_ablation_of_all_rules_reproduces_baseline_exactly |
| zone boundary treated as outside | KILLED | 3 | test_point_in_zone_boundary_inclusive[p1-True] |

## Not covered / not claimed

* Only synthetic data; no real GPS traces. * Streamlit is smoke-tested (renders, key metrics) but not visually inspected by a human. * Performance on millions of pings was not tested (the engine is a plain Python loop, about 1-3 s for 2,894 rows).
