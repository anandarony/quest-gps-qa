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
