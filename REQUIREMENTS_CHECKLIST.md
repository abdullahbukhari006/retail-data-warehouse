# Project requirements and acceptance checklist

Source of requirements: `DWH_Project.pdf`, sections 1–16. This checklist tracks implementation and verification separately through its acceptance evidence. An item is complete only after its acceptance check passes. All implementation items begin pending.

## Repository baseline

- [x] Repository uses branch `main` and remote `https://github.com/abdullahbukhari006/retail-data-warehouse.git`.
- [x] Add a project `.gitignore` for Python environments, credentials, caches, logs, temporary files, and submission archives.
- [x] Establish this requirements checklist.
- [x] Record successful verification evidence against the requirements below as development progresses.

### Verification record

- Step 2: User confirmed two successful executions of `create_dw.sql` in MySQL Workbench and all supplied schema checks passing, including the ten tables, five fact foreign keys, and check constraints.
- Step 3: Python entry point, runtime settings, fresh-per-caller database connections, transaction helper, dependency list, and startup instructions added. Project-local Python 3.13 environment installed with mysql-connector-python 9.7.0 and openpyxl 3.1.5. Dependency consistency, Python compilation, four CLI smoke checks, and mocked connection lifecycle, commit/rollback, password representation, and startup success/error checks passed. User verified an authenticated Python connection to MySQL 8.0.45, database retail_data_warehouse, and the presence of all 10 required project tables. Preparation and processing entry points will be added when their implementations are available.

## Decisions to resolve and document

Step 4 verification: Downloaded and inspected `Data/Online Retail.xlsx`, 541,909 rows and eight columns, with dates from 2010-12-01 08:26 to 2011-12-09 12:50. Workbook integrity and unchanged-copy checksum passed. Source, public-mirror acquisition, partial UCI byte comparison, raw quality counts, field types, identifier conventions, citation, and download date are recorded in `Data/data_notes.md`. Cleaning remains pending. Full UCI/mirror byte equality was not established because the UCI download was interrupted after its matching prefix was verified.

These are proposed interpretations, pending implementation review or instructor clarification.

| Topic | Proposed treatment | Acceptance evidence |
| --- | --- | --- |
| Transaction identity | Preserve each valid source line, including repeated lines. Retain invoice ID and add a stable internal line identifier. | A repeated product line survives preparation and loads exactly once. |
| Store enrichment | Follow section 5.4 and enrich from the five-store in-memory list outside MESHJOIN. | Invoice hash reproduces the expected store. |
| `processing_mode` | Original ingestion path. Staged rows use `Periodic`; core input conversion defaults online rows to `Live`. Existing supplied values remain unchanged through MESHJOIN. | Definitions documented, periodic load verified, and core transaction preservation tested. Thread/shedding preservation remains pending. |
| View wording | Q20 explicitly requires a regular `STORE_QUARTERLY_SALES` view. Section 11 also mentions a materialized view. Seek clarification and document the chosen interpretation. | Executable Q20 and recorded resolution. |
| Reporting dates | Select periods from actual loaded dates and name them in SQL comments. Record partial-year coverage. | Query comments match minimum/maximum loaded dates. |
| Q1 and Q11 | Supply separately numbered answers despite their overlap. | Both sections execute. |
| Analytical conventions | Document tie handling, average purchase grain, zero or missing previous periods, and inclusion of zero-sales days in daily averages. | SQL comments and independently checked examples. |
| Periodic loading | Preserve the hourly loading requirement while joining released groups without blocking online work. Define how completed periodic results reach each hourly load. | Timestamped release, join, and fact-load evidence. |

## Schema and setup — 15 marks

| Done | Requirement | Implementation location | Acceptance evidence |
| --- | --- | --- | --- |
| [x] | Drop previous project tables and `STORE_QUARTERLY_SALES` safely before recreation. | `Create-DW/create_dw.sql` | User confirmed script runs successfully twice in Workbench. |
| [x] | Customer, product, supplier, store, and time dimensions with original IDs, primary keys, and fact foreign keys. | `Create-DW/create_dw.sql` | Schema inspection and valid dimension references. |
| [x] | One fact per transaction line with quantity, revenue, five dimension keys, invoice ID, sales mode, and processing mode. | Schema and `Mesh-Join/retail_dw/warehouse.py` | Line-level reconciliation, including repeated invoice products. |
| [x] | Time columns include date, hour, day, month, quarter, year, day of week, weekday/weekend, season, and half-year using brief definitions. | Schema and warehouse loader | Boundary examples for weekends, seasons, quarters, and halves. |
| [x] | Working tables hold disk-backed masters, periodic staging, and shed transactions with flags and shed time. | Schema and preparation | Table definitions and sample records. |

## Data preparation

Step 6 verification: Generated 278,740 Online and 119,144 Periodic lines from 397,884 cleaned rows, with 4,338 customer and 3,665 product master rows. Independently checked all assignments, 18,532 invoice channel/store pairs, every online output line, and every generated attribute. All four output SHA-256 hashes matched after a complete rerun. Focused checks covered first-country selection, longest-description ties/fallback, publication rollback, concurrent-generation exclusion, and master-load commit/rollback. The actual master CSVs passed the CLI loader using a mocked MySQL connection in 17 batches of at most 500 rows. User confirmed a successful authenticated load of both master tables, reporting 4,338 customer rows (9 pages) and 3,665 product rows (8 pages). A separate Workbench COUNT query has not been reported. Windows denied replacement of existing production CSVs during rerun, including an escalated retry. Generation now reuses byte-identical outputs, and changed-file rollback passed fixture verification.

Step 5 verification: The `clean` entry point read the original workbook and produced 397,884 cleaned lines from 541,909 input lines, removing 144,025. An independent verifier read the workbook's raw XML and matched every retained source-line ID and all eight mapped fields to the CSV, in order, with no extra output lines. All four exclusion counts matched the raw inspection. Focused checks passed for overlapping exclusions, repeated valid lines, missing/numeric descriptions, deterministic reruns, and preserving an existing output after a failed run. The original workbook SHA-256 remained unchanged. Counts and operating instructions are recorded in `Data/data_notes.md` and `ReadMe/README.md`.

| Done | Requirement | Implementation location | Acceptance evidence |
| --- | --- | --- | --- |
| [x] | Read original UCI Online Retail workbook and map all eight source fields. Record citation and download date. | `prepare_data.py`, `Data/data_notes.md` | Source metadata, field mapping, and full-line reconciliation passed. |
| [x] | Programmatically remove cancellations, missing customers, quantity <= 0, and price <= 0. Print counts before and after. | `prepare_data.py` | 541,909 before and 397,884 after; printed, documented, and independently verified. |
| [x] | Assign channel once per invoice using UTF-8 MD5 first eight hexadecimal digits modulo 10; 0–6 online, 7–9 periodic. | `prepare_data.py` | Independently verified all invoice assignments and channel counts. |
| [x] | Use exactly five prescribed stores; online store 1, physical store `2 + hash % 4`. | Preparation and warehouse loader | Every invoice has one verified store assignment; runtime integration follows later. |
| [x] | Generate all customer attributes with exact hash suffixes, list order, and first-cleaned-country rule. | `prepare_data.py` | All 4,338 customer rows independently verified. |
| [x] | Generate product category and supplier using prescribed rules; retain longest description or `UNKNOWN`. | `prepare_data.py` | All 3,665 product rows independently verified. |
| [x] | Export required online and master CSVs with specified fields and load masters to disk-backed database tables. | Preparation and `Data/` | CSV fields independently checked; user confirmed successful authenticated loading of 4,338 customer and 3,665 product rows. |
| [x] | Sort periodic rows by event time, divide into 48 groups, store release hour and released flag. | Preparation and staging | Independently reconciled 119,144 lines. User confirmed authenticated staging load and pre-commit database group/flag checks. Smallest 2,482, largest 2,483. |
| [x] | Calculate actual 500-row partition counts for both masters. | Preparation | Counts printed and included in report. |

## MESHJOIN and execution — 30 marks

Step 10 verification: Added a stable online/fresh-periodic/replay priority buffer, transactional ShedStore, overload checks at chunk admission, and non-peak replay owned by MeshJoinThread. A separate shed-table connection keeps its commits independent of master scans. PeriodicThread validates pre-existing shed records and includes them in group acknowledgements without submitting them twice. The finite verification switches to non-peak after selected online input completes when eligible disk work remains. All 25 regression tests passed, covering priority order, full-buffer gating, failed writes retaining memory ownership, idempotent persistence, identity conflicts, acknowledgement failures, restart, and peak-to-non-peak recovery. The user's live MySQL stress check submitted/enriched 10,000 online and 2,483 periodic rows at a configured peak ceiling of 1,000,000/second, default non-peak rate 10, and default 60-second hour. It shed 1,785 periodic rows and replayed/acknowledged all 1,785, leaving disk/buffer/H/queue at zero, in 1.249 seconds. A full accelerated fixture run processed 278,740 online and 119,144 periodic rows across all 48 one-second groups, shed/replayed/acknowledged 1,715 rows, and finished in 60.0 seconds with empty components and closed worker-owned connections. Both configured rates in that fixture run were 1,000,000/second. Release intervals and verification rollbacks passed. Durable fact loading remains for step 11.

Step 8 verification: Core MESHJOIN and the `check-join` CLI are implemented. Ten regression tests passed using disposable disk-backed masters, including unequal 3/2 partition cycles, short final pages, 1,501 staggered transactions with a final one-row chunk, multimap references, bounded buffering, missing master matches, output retry, and partial-read failure. Full CLI verification with disk-backed fixture masters populated from the actual generated CSVs enriched 397,884 transactions in 804 iterations, using 804 pages per master (1,608 bounded queries), and drained all components. After an initial MySQL 1045 authentication rejection, the user retried successfully. The authenticated MySQL check enriched 1,000 transactions in 10 iterations, read 10 pages per master with actual partition counts of 9/8, and drained Stream buffer, H, and Queue to zero. Three-thread integration, priority/shedding, and warehouse loading remain later stages.

Step 9 verification: OnlineThread, PeriodicThread, and MeshJoinThread run independently through a shared 1,000-line queue. Each database worker creates, uses, and closes its own connection. The user confirmed a live MySQL run with 100/100 online and 4,966/4,966 periodic submitted/enriched lines, two groups, empty final buffers, and elapsed time 11.146 seconds at 10 online transactions/second and a two-second hour. All 17 regression tests passed, including blocked-scan producer independence, speed changes, delayed database setup, full-buffer cancellation, coordinator drain on cancellation, failure rollback, and acknowledgement-gated release commits. A complete fixture run enriched 278,740 online and 119,144 periodic rows across all 48 one-second groups in 47.522 seconds with an accelerated configured online rate of 1,000,000/second. All 48 extraction intervals passed, both connections closed, and verification flags were rolled back. An initial full-run timing assertion exposed a deadline anchored before database setup. Anchoring at the extraction request fixed it, and both the delayed-setup regression and full rerun passed. Verification currently uses an enrichment/count sink. Durable warehouse writes, priority, shedding, and the operator command loop follow later.

| Done | Requirement | Implementation location | Acceptance evidence |
| --- | --- | --- | --- |
| [x] | Prompt for host, port, database, username, password, and online file path. Never hardcode the password. | `main.py`, `database.py` | User authenticated the thread check and supplied the online-file path; source inspection passed. |
| [x] | Print arrival mode, peak/non-peak rates, simulated-hour scale, and buffer limit at startup. | `main.py`, `settings.py` | User confirmed startup output for live join and thread checks. |
| [x] | Three independent threads named OnlineThread, MeshJoinThread, and PeriodicThread. | `online.py`, `meshjoin.py`, `periodic.py` | Blocked-scan concurrency test and authenticated two-group run passed. |
| [x] | Stream buffer limit 1,000; chunk size up to 500; disk pages up to 500. | `meshjoin.py`, settings | Capacity, partial-page/chunk, and full-input checks passed. |
| [x] | Multimap H contains actual transactions; queue contains references to chunks in arrival order. | `meshjoin.py` | Shared object identity and repeated-key/output-order checks passed. |
| [x] | Read one page of each master per iteration, scan cyclically, and retain chunks for full coverage of both masters. Avoid full master dictionaries and per-transaction master queries. | `meshjoin.py` | Unequal cycles and final-page checks passed; full-input fixture run used only bounded page reads. Authenticated MySQL 1,000-row check also passed. |
| [x] | Enrich each transaction from both masters, preserving transactional fields and flags. | `meshjoin.py` | Immutable source transactions, both master matches, and supplier attributes verified in core checks. Runtime integration follows later. |
| [x] | Default online rates 80 peak and 10 non-peak; command-line rate control and runtime changes supported. | `online.py`, settings, commands | Defaults/CLI overrides and RunControl runtime speed switching verified in step 9. Step 12 command/rate/status regression checks passed; authenticated MySQL console verification passed. |
| [x] | Release one periodic group each simulated hour; default 60 seconds; group 0 may release immediately. No duplicate group extraction. | `periodic.py` | Full 48-group fixture run verified all extraction intervals. Default scale is 60 seconds; custom scales print at startup. Live two-group check passed. |
| [x] | Online first, periodic second, shed replay last. Shed only cleaned periodic rows under actual overload. | `buffer.py`, `meshjoin.py`, `shedding.py` | Priority/saturation/failure tests passed. User verified 1,785 periodic rows shed under peak stress; defaults 80/10 and test override recorded. |
| [x] | Recover shed rows during available non-peak capacity without loss or duplication. | `meshjoin.py`, `shedding.py`, database | User verified 1,785 replayed/acknowledged and zero final disk backlog. Restart and failed-ack tests passed. |
| [x] | Compute revenue only after enrichment; insert dimensions before facts and preserve required modes. | `warehouse.py` | Decimal totals and foreign-key checks. |
| [x] | Support online, periodic, both, peak, non-peak, status, and quit while running. | `commands.py`, `main.py` | All commands exercised with status in disposable threaded regression checks; authenticated MySQL console check passed. |
| [x] | Status prints mode, speed, buffer count, H count, and shed disk count. | `commands.py` | Status values checked against control counters and buffer contents; authenticated MySQL console check passed. |
| [x] | Shutdown closes threads/connections consistently; retries and replay cannot duplicate completed sales. | Runtime and loader | Shutdown and retry checks. |
| [x] | Full required run loads every cleaned source line exactly once. | End-to-end run | Identity, count, quantity, and revenue reconciliation by channel; empty pending queues. |

## Step 11 verified warehouse loading

The authenticated full MySQL run completed in 94.375 seconds with 397,884 facts, 5,167,812 units, and stored revenue 8,911,407.904000. It enriched 278,740 online and 116,661 remaining periodic rows and visited all 48 hourly groups. The first 2,483 periodic facts were already committed by the earlier small run. This run inserted 89,033 online and 116,661 periodic facts and verified 189,707 existing online facts. Existing facts plus new facts reconcile exactly with the final warehouse count.

The run shed 793 new rows and replayed/acknowledged 2,483 rows. Replay can include durable backlog from earlier attempts, so these counters need not match within a resumed run. Final shed rows, stream buffer, H transactions, queue chunks, and prepared periodic results were all zero.

The initial live subset loaded 2,583 facts, 32,492 units, and revenue 63,009.850000 in 3.028 seconds. Its rerun inserted zero facts and preserved those totals. All 32 regression tests passed. A separate complete disk-backed fixture run finished in 87.272 seconds and independently compared every fact and dimension attribute against the assigned source, including all source IDs, modes, quantities, decimal revenue, calendar attributes, and 48 release intervals. The live full run confirms the expected aggregate totals; its every-line comparison was performed in the disposable fixture.

Online results load directly after dimension resolution. Periodic results persist in bounded batches to `periodic_join_results`, then publish to facts with staging release flags in one hourly transaction. The additive SQL script creates this working table without recreating the existing warehouse. Operator commands and OLAP analyses remain pending.

## SQL analysis — 30 marks

All answers belong in `Queries-DW/analysis_queries.sql`, numbered Q1–Q20. Use stored fact revenue, stored time attributes, and explicit loaded-date comments. Growth and volatility mean percentage change from the previous period.

| Done | Query | Acceptance evidence |
| --- | --- | --- |
| [ ] | Q1 Monthly top five products split by weekday/weekend | Ranking groups and monthly totals checked. |
| [ ] | Q2 Purchase amount by gender, age, city category | Aggregation reconciles with facts. |
| [ ] | Q3 Category sales by occupation | Category/occupation totals checked. |
| [ ] | Q4 Gender/age purchases by quarter | Quarterly totals checked. |
| [ ] | Q5 Top five occupations per category | Ranking restarts per category. |
| [ ] | Q6 City category/marital status over latest six loaded months | Date boundaries and monthly breakdown checked. |
| [ ] | Q7 Average purchase by stay duration/gender | Averaging grain documented and checked. |
| [ ] | Q8 Rank city categories A/B/C within product categories | Exactly the prescribed categories used. |
| [ ] | Q9 Monthly category growth | Previous-period and zero-denominator handling checked. |
| [ ] | Q10 Weekday/weekend sales by age | Totals checked for selected year. |
| [ ] | Q11 Monthly top five products split by weekday/weekend | Separate labeled executable answer. |
| [ ] | Q12 Quarterly store growth | Store partitions and quarter order checked. |
| [ ] | Q13 Supplier contribution by store and product name | Required grouping hierarchy checked. |
| [ ] | Q14 Product sales by season | Prescribed season boundaries checked. |
| [ ] | Q15 Monthly store/supplier revenue volatility | Percentage change independently calculated. |
| [ ] | Q16 Top five product pairs | Unordered pairs counted once per distinct invoice. |
| [ ] | Q17 Yearly revenue using ROLLUP | Product, supplier, store, and grand totals reconcile. |
| [ ] | Q18 Product quantity/revenue by half and full year | Half-year totals sum to full year. |
| [ ] | Q19 Daily product revenue above twice its daily average | Daily-average convention documented; actual spikes discussed. |
| [ ] | Q20 Create and read STORE_QUARTERLY_SALES | Store quarterly totals checked; ordering on reading SELECT. |

## Bonus — outside the 100 marks

| Done | Requirement | Implementation location | Acceptance evidence |
| --- | --- | --- | --- |
| [ ] | Finish both channels, shedding, and all 20 questions before adding bonus. | Required-run evidence | Required acceptance checks pass first. |
| [ ] | Cache completed online/Live join results in a database table; periodic results retain hourly DW loading. | Schema and `warehouse.py` | Cache contains only online results. |
| [ ] | During a slow period copy cached rows to DW and delete copied rows consistently. | `warehouse.py` | Cache fills/drains; retry-safe transfer; no lost sales. |
| [ ] | Explain selected loading mode in ReadMe. | `ReadMe/README.md` | Instructions reproduce bonus behavior. |
| [ ] | Bonus and direct-load runs produce equivalent final facts. | End-to-end verification | Line IDs, counts, quantities, and revenue match. |

## Report, instructions, and submission — 25 marks

| Done | Requirement | Deliverable | Acceptance evidence |
| --- | --- | --- | --- |
| [ ] | Overview, actual star diagram/explanation, MESHJOIN explanation and three shortcomings. | `Project-Report/` | Document matches implementation. |
| [ ] | Cleaning counts, channel rule, partition counts, periodic group range, peak/non-peak shed counts, Q19 spikes, and reflection. | Report and run evidence | Measured values agree with complete run. |
| [ ] | Dataset citation and actual download date. | Report, ReadMe, data note | Required citation appears in all locations. |
| [ ] | Database creation, data preparation, startup, all commands, queries, scale, and loading-mode instructions. | `ReadMe/README.md` | Fresh extraction works using instructions alone. |
| [ ] | Six required folders and three required CSVs plus data note included. | Submission ZIP | Archive inventory verified. |
| [ ] | Full-name/student-ID ZIP name; source and report prepared individually; no credentials or local paths needed to operate. | Submission ZIP | Final package review. |
| [ ] | Submit on Google Classroom by 13 November 2026, 23:00 and verify receipt. | User submission | Submission confirmation. |

The implementation is complete when every applicable item passes with recorded evidence. Submission remains a separate user action unless explicitly authorized.

## Step 12 operator console verification

Interactive `run` accepts online, periodic, both, peak, non-peak, status, and quit, plus help and optional positive rates after the speed commands. It starts in both mode unless `--mode` selects another mode, and retains the console after input completion. `--batch` preserves finite execution. Commands gate new input while accepted work drains. Periodic group order and minimum release interval remain intact across pauses. Status reads shared worker counters without sharing worker database connections. Interactive speed remains operator controlled, and quit switches to non-peak for drain/recovery.

All 39 regression tests passed using D-drive temporary fixtures. New tests cover mode/rate parsing and validation, reported counters, EOF, pause/resume identity, periodic gating, three-worker mode transitions, console persistence after completion, and active quit with reconciled counts and closed connections. Authenticated MySQL operator verification passed in 41.079 seconds. Initial periodic mode kept online submissions at zero. Commands online, peak 120, non-peak 120, periodic, both, status, and quit reported the selected modes and rates. All 100 online rows were enriched and verified as existing facts; no new facts were inserted. The two selected periodic groups were already released, so no periodic rows were resubmitted. The console remained available after completion and quit returned cleanly. Final buffer, H, queue, shed backlog, and prepared periodic results were zero. Warehouse totals remained 397,884 facts, 5,167,812 units, and revenue 8,911,407.904000. Unreleased-group pause/resume and active-input shutdown are covered by the disposable regression tests.

## Step 13 warehouse reconciliation

Implemented `reconcile` as a complete-source, read-only warehouse audit. Expectations are reconstructed on disk from cleaned/assigned input and the prescribed rules, with online/master export comparisons. All fact identities and required fields, all five dimensions, database masters, per-channel count/quantity/revenue, calendar fields, periodic group placement/release flags, and empty shed/prepared tables are checked. MySQL uses a read-only consistent snapshot and 500-row pages. Mismatches produce bounded diagnostic examples and nonzero exit status; source/database errors also fail the command.

Regression verification includes an accurate multi-page fixture and intentional corruption that preserves grand totals, as well as missing/extra/duplicate identities, orphan references, wrong modes/calendar/master attributes, staging errors, backlog, and inconsistent source exports. The complete disposable warehouse passed the new reconciliation command against all 397,884 source lines. Online totals were 278,740 facts, 3,526,585 units, and revenue 6,075,186.073000. Periodic totals were 119,144 facts, 1,641,227 units, and revenue 2,836,221.831000. Combined totals were 5,167,812 units and revenue 8,911,407.904000. All 4,338 customer, 3,665 product, five supplier, five store, and 17,282 time dimension rows matched. Event-time coverage was 2010-12-01 08:26:00 through 2011-12-09 12:50:00. All 48 periodic groups matched the sorted source with sizes 2,482–2,483; released flags and empty shed/prepared tables passed. All 46 regression tests passed. Authenticated full MySQL reconciliation also passed. The user confirmed matching facts, quantities, and decimal revenue for both channels and the complete warehouse, all 25,295 dimension rows (4,338 customer, 3,665 product, five supplier, five store, and 17,282 time rows), source event-time coverage, all 48 group assignments and released flags, and empty durable backlogs. Step 13 is complete.
