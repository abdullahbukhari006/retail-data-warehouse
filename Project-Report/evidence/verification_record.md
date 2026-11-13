# Verified project results

Prepared 13 November 2026 for the project report. Figures below transcribe authenticated MySQL terminal outcomes supplied during development and the recorded automated fixture checks. They are not screenshots or newly measured runs.

## Preparation and warehouse reconciliation

| Measure | Verified result |
| --- | ---: |
| Original source lines | 541,909 |
| Retained lines | 397,884 |
| Removed lines | 144,025 |
| Customer masters and pages | 4,338 and 9 |
| Product masters and pages | 3,665 and 8 |
| Periodic groups | 48 |
| Smallest and largest groups | 2,482 and 2,483 |
| Online facts / units / revenue GBP | 278,740 / 3,526,585 / 6,075,186.073000 |
| Periodic facts / units / revenue GBP | 119,144 / 1,641,227 / 2,836,221.831000 |
| Complete facts / units / revenue GBP | 397,884 / 5,167,812 / 8,911,407.904000 |
| Customer / product / supplier / store / time dimension rows | 4,338 / 3,665 / 5 / 5 / 17,282 |
| Event-time range | 2010-12-01 08:26:00 through 2011-12-09 12:50:00 |
| Final shed / prepared periodic rows | 0 / 0 |

Native `reconcile` returned PASS for every source line, required fact field, master/dimension attribute, calendar part, group assignment/released flag, and channel total.

## Loading and shedding

- Peak stress verification enriched 10,000 Online and 2,483 Periodic lines, shed/replayed/acknowledged 1,785, and drained components in 1.249 seconds. Peak ceiling 1,000,000/second, non-peak 10, normal 60-second hour. Release flags were rolled back after verification.
- Small durable load enriched 100 Online and 2,483 Periodic lines, shed/replayed/acknowledged 1,000, and finished in 3.028 seconds. Peak 80/second, non-peak 10, two-second hour. Warehouse totals 2,583 facts, 32,492 units, GBP 63,009.850000. Its repeat inserted zero new facts and retained totals.
- Resumed full durable load enriched 278,740 Online and 116,661 remaining Periodic lines, inserted 89,033 Online and 116,661 Periodic facts, and verified 189,707 existing Online facts. The first 2,483 Periodic facts were already committed. Peak ceiling 1,000,000/second, non-peak 10, one-second hour. Newly shed 793, replayed/acknowledged 2,483 including persisted backlog, final shed zero. Elapsed 94.375 seconds, covering this resumed run rather than initial loading from an empty warehouse.
- Interactive console verification processed 100 existing Online facts, left complete totals unchanged, displayed mode/rate transitions, and closed cleanly with zero pending components.

## Native OLAP verification

The final `check-queries` run on MySQL 8.0.45 ended `OLAP verification: PASS (20/20)` on 13 November 2026. Returned row counts were:

| Question | Rows |
| --- | ---: |
| Q1 | 120 |
| Q2 | 42 |
| Q3 | 100 |
| Q4 | 56 |
| Q5 | 50 |
| Q6 | 36 |
| Q7 | 10 |
| Q8 | 30 |
| Q9 | 120 |
| Q10 | 14 |
| Q11 | 120 |
| Q12 | 20 |
| Q13 | 15,010 |
| Q14 | 11,921 |
| Q15 | 300 |
| Q16 | 5 |
| Q17 | 14,674 |
| Q18 | 3,596 |
| Q19 | 114,713 |
| Q20 | 25 |

Q13's first native run returned 15,002 merged-name groups under PAD SPACE comparison. Exact-byte grouping fixed it, and a dedicated full-source regression reproduced 15,002 before and 15,010 after the fix. All 51 regression tests passed before report preparation. Independent complete-source fixtures also passed all query comparisons, using documented test-only SQLite adapters for MySQL dialect differences. Calculation conventions and measured Q19 examples are in Queries-DW/verification_notes.md.

## Report coverage and layout review

The report contains the overview (page 1), cleaning/provenance (2), channel and generation rules, partition counts and group sizes (3), actual star diagram and grain (4), join algorithm and threads (5), durable loading and measured shed/replay counts (6), reconciliation and all query outcomes (7), Q19 findings (8), three shortcomings and reflection (9), and reproduction plus required source citation/download date (10).

The editable report was exported with Microsoft Word because the packaged LibreOffice renderer was unavailable on this Windows host. All ten final page images were visually inspected for readable text, schema relationships, table alignment, clipping, and page breaks. Required identity and measured values were also checked in extracted PDF text. That initial report described the direct loading baseline. The report has since been extended with the implemented and verified bonus, as recorded below.

## Bonus cache verification

The complete-source deferred-transfer fixture also passed. A direct baseline was recorded, then all 278,740 Online/Live results were held in cache while 119,144 Periodic facts remained published. Batches of at most 500 transferred all 278,740 as new Online facts and removed their cache entries. Every one of the 397,884 final fact payloads matched the direct baseline, and complete source reconciliation passed with exact channel quantities/revenue, all dimension attributes, all 48 groups, and zero cache/prepared/shed rows. Fresh-row deferral and Periodic publication during peak were independently exercised by a three-worker interactive fixture. All 62 regression tests passed.

Authenticated MySQL cache verification passed on 13 November 2026. At peak 80/second, Online was 100/100, cache held 100, and observed cache high-water was 100. Switching to non-peak 10/second transferred and removed all 100 cached retries. Existing fact checks were 100 at ingestion and 100 at transfer; new facts were zero. Final warehouse remained 397,884 facts, 5,167,812 units, GBP 8,911,407.904000, with zero Online cache, prepared Periodic, shed, stream, H, and queue counts. The one selected Periodic group was already released, so it resubmitted no rows. Elapsed 47.116 seconds includes operator interaction and is not a throughput measurement.

The current report has eleven pages. Section 11 explains cache schema, Online/Live-only admission, 500-row atomic transfers, peak/non-peak scheduling, retry/restart behavior, native matching-retry evidence, and fresh/full-source fixture equivalence. Pages 1, 6, 7, and 10 were updated and page 11 added. They were rendered through Word and visually reviewed; pages 2–5 and 8–9 remained pixel-identical to the previously reviewed report.
