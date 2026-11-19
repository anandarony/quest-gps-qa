# Final execution report (for the candidate; not a Match Hire deliverable)

Date of work: 2026-11-16. Synthetic data only; the baseline is an intentionally defective reference, not employer code.

## 1. Executive summary

* **Quest accepted:** yes. The "Accept & Start Quest" button was clicked and the page then showed a countdown (4d 23h 59m) and the upload section.
* **Technical assessment:** completed and verified by execution: dataset (2,894 pings), 8 reproducible defects, QA tool + dashboard, root-cause evidence, corrected engine, before/after analysis, 127 passing tests, regression that fails on the baseline and passes on the fix, independent oracle, mutation check.
* **Headline:** baseline 434.25 km vs correct 134.32 km (+223 %), USD 239.94 over-billed at an invented USD 0.80/km.
* **Overall status:** engineering done; **submission NOT done.** Outstanding: record the Loom, complete the candidate-review checklist, host/link the repo, upload two files by hand, fill two link placeholders, submit.

## 2. Quest acceptance

| Question | Answer |
|---|---|
| Was Accept & Start Quest clicked? | Yes |
| Was acceptance confirmed? | Yes: page changed from "Ready to start?" to a live countdown plus "Submit your work" with "0 of 3 required attached" |
| Did the timer start? | Yes: "Time remaining 4d 23h 59m" right after the click |
| Website issues | None. One content discrepancy: page says daily cut 00:00 local (UTC+9); your brief says 00:00 UTC. Both implemented. |

## 3. Work completion summary

| Workstream | Status | Evidence |
|---|---|---|
| Quest acceptance | Completed | countdown visible on the page |
| Environment setup | Completed | Python 3.13; `requirements.txt` |
| Dataset generation | Completed | `data/synthetic_gps_pings.csv` (2,894 rows), `ping_labels.csv`, `seeded_defects.json` |
| Defect investigation | Completed | `reports/defect_report.md` (8 defects, evidence rows) |
| QA tool development | Completed | `src/`, `app.py` (smoke-tested + live server health 200) |
| Root-cause analysis | Completed | defect report + `intent.md` section 4 |
| Defect correction | Completed | `src/distance_calculator.py` |
| Regression testing | Completed | 127 passed / 0 failed / 0 skipped; baseline 2 failed, corrected 2 passed |
| Before/after analysis | Completed | `reports/before_after_comparison.csv`, `final-directive.md` section 10 |
| Documentation | Completed | README, intent.md, final-directive.md, data dictionary, AI log, Loom script |
| Submission | **Not done** | see section 11 |

## 4. Defect investigation results

Full detail: `reports/defect_report.md`. Ranked by absolute driver-day exposure (impacts are marginal and overlap):

| Rank | ID | Defect | Severity | Net km | USD | Root cause (one line) |
|---|---|---|---|---|---|---|
| 1 | D-ORDER | late/out-of-order pings handled in arrival order | Critical | +168.45 | +134.76 | no sort by `ts_utc`; late ping joined to wrong neighbours (jumps up to 10 km) |
| 2 | D-SPEED | > 150 km/h pings billed | Critical | +32.86 | +26.29 | no speed check; on-road teleports |
| 3 | D-GAP | billed across gaps | High | +8.79 | +7.03 | rows joined regardless of elapsed time |
| 4 | D-NOISE | > 100 m off-road billed | Medium | +2.33 | +1.86 | no road-distance check (partly masked by D-SPEED) |
| 5 | D-ZONE | END ping decides zone | Medium | -0.43 (abs 2.09) | -0.35 | whole segment judged by one point |
| 6 | D-PREC | 4-decimal rounding | Medium | -0.02 (abs 1.04) | -0.01 | grid snapping; flips 22 pings' accept/reject incl. exact-edge cases |
| 7 | D-DAY | midnight segment on one day | Medium | 0.00 (abs 0.60) | 0.00 | date from end ping only |
| 8 | D-DUP | duplicates double counted | Medium | +0.37 | +0.29 | no (driver, ts) de-dup |

First fix chosen: D-ORDER (largest exposure, unavoidable in production, non-determinism). All eight were fixed.

## 5. Dataset statistics

| Statistic | Value |
|---|---|
| Total records | 2,894 (12 drivers, 3 zones, 2026-03-14/15) |
| Valid (accepted) records | 2,694 |
| Rejected | 200 = 72 duplicate + 72 off-road + 48 impossible speed + 8 invalid fields |
| Out-of-order records (file order) | 105 raw, 104 among structurally valid rows |
| Late-arriving records | 104 (120-900 s late; 8 of them arrive after the daily cut) |
| GPS noise records (> 100 m) | 72 (60 noise + 12 at 100.5 m); plus 12 at exactly 100.0 m kept |
| Impossible-speed records | 48 (36 jumps + 12 at 150.5 km/h); plus 12 at exactly 150.0 km/h kept |
| UTC-boundary records | 46 within 60 s of 00:00 UTC; 47 within 60 s of 00:00 UTC+9 |
| Missing intervals | 30 gaps: 12 x 30 s (billed), 12 x 100 s and 6 x 300 s (not billed) |
| Campaign-zone records | 1,057 accepted pings inside a zone (1,128 incl. rejected) |

## 6. QA tool results

* **Architecture:** validator -> de-dup -> event-time sort -> road/speed filters -> segments -> gap rule -> zone clip -> day split -> audit + daily km; separate oracle and defect detector (ablation).
* **Features:** all items A-E of the brief (validator, quality checker, zone validator, distance engine, dashboard).
* **Run:** `streamlit run app.py` (sidebar: 00:00 UTC / UTC+9, CSV upload).
* **Dashboard:** total/valid/rejected/duplicate/noise/speed counts; distance before/after/difference; per-driver daily table and chart; defect summary with root cause and evidence; financial impact; rejected-ping audit; map.
* **AI-assisted capabilities (honest):** AI wrote and debugged the tool. No AI runs inside it. What it got wrong and how that was fixed: `reports/ai_usage_log.md`.
* **Limitations:** `final-directive.md` section 12.

## 7. Before-and-after results

| | Original | Corrected | Difference |
|---|---|---|---|
| Distance | 434.254 km | 134.324 km | +299.931 km (+223.3 %) |
| Advertiser bill (USD 0.80/km) | 347.40 | 107.46 | **239.94 over-billed** |
| Driver payout (40 %) | 138.96 | 42.98 | 95.98 error |

Per driver and day (16 rows): `reports/before_after_comparison.csv` and `final-directive.md` section 10. Largest: D007 on 2026-03-14, 46.461 -> 7.558 km (+514.7 %).

## 8. Automated test results

| Total | Passed | Failed | Skipped |
|---|---|---|---|
| 127 | 127 | 0 | 0 |

* Baseline regression outcome: `test_matches_oracle_all_days` **2 failed** (worst driver-day 42.2 km off).
* Corrected regression outcome: **2 passed**.
* Oracle comparison: max error about 1e-9 km (tolerance 1e-6) for both day cuts, per zone, per ping decision, and 3 other seeds.
* Mutation check: 9 of 9 injected bugs caught.
* Outstanding failures: none.

## 9. Deliverables

Repository: `/home/claude/quest-gps-qa/` (zip: `/home/claude/quest-gps-qa.zip`)

| Required deliverable | File |
|---|---|
| 1. Loom video | **not recorded**; script `reports/loom_script.md` |
| 2. intent.md | `quest-gps-qa/intent.md` |
| 3. final-directive.md | `quest-gps-qa/final-directive.md` |
| Source | `src/`, `baseline/`, `app.py`, `tests/` |
| Data | `data/` |
| Reports | `reports/defect_report.md`, `before_after_comparison.csv`, `test_results.md`, `mutation_check.md`, `progress_report.md`, `ai_usage_log.md`, `final_execution_report.md` |

## 10. Outstanding issues

1. Record the Loom (cannot be done by the agent).
2. Fill the two placeholders in `final-directive.md` section 14 (repository/zip link, Loom link).
3. Upload `intent.md` and `final-directive.md` manually (the browser tool in this session cannot upload agent-generated files).
4. Complete the review checklist and enter your real hours in `reports/ai_usage_log.md`.
5. Decide which daily cut (UTC or UTC+9) to present first; confirm with the reviewer if unsure.
6. Open every link before submitting; the Quest requires reviewer access.

## 11. Match Hire submission status

| Stage | Status |
|---|---|
| Prepared | **Yes** for intent.md, final-directive.md (two link placeholders open), repo zip. **No** for the Loom. |
| Uploaded | **No** (0 of 3 required items attached on the page) |
| Submitted | **No** |
| Submission confirmed | **No** |

The site itself says "You cannot submit yet. Still to attach: Loom video, intent.md, Final directive.md".

## 12. Recommendations

1. Lead the Loom with the one-sentence result (434 -> 134 km, fails/passes) and the D-ORDER evidence line; the reviewer weights "problem actually gone" at 30 %.
2. State clearly in the video which parts the AI produced and what *you* checked, changed or rejected; the AI log lists real corrections, add your own.
3. Be explicit about the UTC vs UTC+9 cut; point to the two parametrised oracle tests.
4. Show the mutation check: it answers "would your tests notice a regression?".
5. Mention the open risks you did not solve (first-ping poisoning, gap policy, late restatement) before the reviewer asks.
6. Do a clean-room run on your own machine (fresh venv, `pip install -r requirements.txt`, `pytest`) before recording, so that "runs for someone else" is true.
7. Keep the repository private/unlisted and share with the reviewer only through the Match Hire process.
