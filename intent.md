# intent.md - problems found, how they were ranked, what I chose and what I left out

> Synthetic data throughout. The "original" calculation is an intentionally defective baseline written for this Quest, not employer code.

## 1. Business problem

Advertisers pay per kilometre driven inside a campaign zone, computed from phone GPS pings every 10 seconds. If the distance job is wrong, advertisers are billed for kilometres that were never driven (or never inside the zone) and drivers are paid on the same wrong number. On my seeded dataset the defective baseline reports **434.25 km** where the correct answer is **134.32 km**: **+223 %**, or **USD 239.94 of over-billing** at an invented USD 0.80/km (drivers over-paid by USD 95.98 at an invented 40 % share).

## 2. How I investigated

1. Turned each business rule into something testable: duplicates, event-time ordering, UTC cut, > 100 m, > 150 km/h, zone entry/exit, precision, gaps.
2. Generated a deterministic dataset (2,894 pings) where I know the right answer for every ping (ground-truth labels) and seeded each fault, including the exact edges (100.0 m / 100.5 m, 150.0 / 150.5 km/h, 30 s gap, a ping exactly at the cut).
3. Wrote a deliberately wrong baseline, then a corrected engine.
4. **Measured** each defect: re-introduce exactly one defect into the corrected engine and compare with the corrected result per driver and day (ablation). Check that all defects together reproduce the baseline exactly (they do, to 1e-9 km), so the attribution is faithful.
5. Checked the corrected engine against an independent oracle (different code, shapely geometry, ground-truth labels), and against 3 other random seeds.
6. Injected 9 deliberate bugs into the corrected engine to prove the tests would notice (9 of 9 caught).

## 3. Defects found, ranked

Ranked by absolute exposure per driver-day (money = km x USD 0.80; positive = baseline over-bills). Evidence for each is in `reports/defect_report.md`.

| Rank | ID | Defect | Records | Net km | Advertiser USD | Severity |
|---|---|---|---|---|---|---|
| 1 | D-ORDER | Late / out-of-order pings processed in arrival order | 104 | +168.45 | +134.76 | Critical (P0) |
| 2 | D-SPEED | Pings implying > 150 km/h are billed | 48 | +32.86 | +26.29 | Critical (P0) |
| 3 | D-GAP | Straight line billed across missing-ping gaps | 18 | +8.79 | +7.03 | High (P1) |
| 4 | D-NOISE | Pings > 100 m off the road are billed | 72 | +2.33 | +1.86 | Medium (P2) |
| 5 | D-ZONE | Zone entry/exit decided by the END ping only | 64 | -0.43 (abs 2.09) | -0.35 | Medium (P2) |
| 6 | D-PREC | Coordinates rounded to 4 decimals | 2,694 | -0.02 (abs 1.04) | -0.01 | Medium (P2) |
| 7 | D-DAY | Midnight-straddling segment given wholly to one day | 4 | 0.00 (abs 0.60) | 0.00 | Medium (P2) |
| 8 | D-DUP | Duplicate pings double counted | 72 | +0.37 | +0.29 | Medium (P2) |

Notes on the ranking:
* The impacts are **marginal** and overlap: a 400 m off-road ping also implies > 150 km/h, so removing only the off-road rule changes little. That is why D-NOISE looks small on its own; the rules protect each other. The eight together reproduce the whole baseline error.
* D-DAY and D-ZONE have a near-zero **net** effect but still make individual daily bills wrong (km moved between days, entry segments over-billed, exit segments under-billed). I ranked them by *absolute* driver-day km, not net.
* D-ORDER is also a data-integrity problem: the answer changes depending on the order rows arrive in. After the fix, shuffling the input rows leaves every number unchanged (tested).

## 4. Root causes (short; detail and evidence in the defect report)

* **D-ORDER**: the loop walks the file in arrival order and never sorts by `ts_utc`. A ping that arrives minutes late is joined to the wrong neighbours, so the path jumps up to about 10 km back and forth. Worst real example in the data: `P002422->P002429`, a 10,107 m straight-line jump between two pings that are 10 s apart in event time.
* **D-SPEED**: no speed check at all; a teleport that stays on the road adds an out-and-back chord of 0.8-2 km.
* **D-GAP**: consecutive rows are joined whatever the elapsed time.
* **D-NOISE**: no distance-to-road check.
* **D-ZONE**: `zone.contains(end ping)` decides for the whole segment.
* **D-PREC**: `df['lat'].round(4)` before measuring (also flips edge cases such as the exactly-100 m ping).
* **D-DAY**: report date taken from the end ping only.
* **D-DUP**: no de-duplication on (driver, timestamp).

## 5. Defect chosen to fix FIRST, and why

**D-ORDER (late / out-of-order pings).** It is the largest financial exposure by a wide margin (+168 km, USD 135, 56 % of the baseline's total over-count), it is unavoidable in production (phones go through tunnels and retry uploads), and it silently makes results non-deterministic. Fixing it is also the foundation for the other rules: speed, gap and day-split logic are only meaningful on correctly ordered pings. I then fixed the rest as well, in priority order, because the Quest asks that the *problem is gone*, not just the first item: the corrected engine implements all eight rules and the regression test covers each.

## 6. What I excluded, and why

| Excluded | Why |
|---|---|
| Real road networks / map matching, real GPS traces | Out of scope for 6-10 h and would bring non-synthetic data. Roads are 3 synthetic polylines. |
| Non-convex zones, zones with holes | The clipping algorithm needs convex polygons; documented as a limit. |
| Interpolating across gaps | Billing invented kilometres is worse than not billing; assumption A3 (> 30 s not billed) is explicit and tested. A product owner may choose differently. |
| Restating a day that was already invoiced when a very late ping arrives (days late) | Needs a business policy (re-issue? credit note?). The engine places the ping on its own day; the restatement workflow is not built. |
| A bad **first** ping of a trip poisoning the speed check | Speed is compared with the last accepted ping; a bad first ping would cascade. Not seeded (first 3 pings of each driver are always clean). Listed as an open risk. |
| Daylight-saving or leap-second effects | Everything is UTC; UTC+9 has no DST. |
| Real rates and real driver pay | Invented numbers (USD 0.80/km, 40 %) used only to size the impact. |

## 7. Honest scope statement

All numbers come from one synthetic dataset (plus three extra seeds checked against the oracle). They show the *method* and the *size of the failure modes*, not a claim about any real platform. The Loom video is **not** recorded by this document; a script is in `reports/loom_script.md`.
