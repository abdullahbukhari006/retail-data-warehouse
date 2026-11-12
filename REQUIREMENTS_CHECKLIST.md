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
| `processing_mode` | Step 7 defines it as the original ingestion path, with `Periodic` for staged rows. Online path semantics and preservation will be integrated with the runtime. | Periodic definition documented in ReadMe and prepared/staged rows verified. Runtime preservation remains pending. |
| View wording | Q20 explicitly requires a regular `STORE_QUARTERLY_SALES` view. Section 11 also mentions a materialized view. Seek clarification and document the chosen interpretation. | Executable Q20 and recorded resolution. |
| Reporting dates | Select periods from actual loaded dates and name them in SQL comments. Record partial-year coverage. | Query comments match minimum/maximum loaded dates. |
| Q1 and Q11 | Supply separately numbered answers despite their overlap. | Both sections execute. |
| Analytical conventions | Document tie handling, average purchase grain, zero or missing previous periods, and inclusion of zero-sales days in daily averages. | SQL comments and independently checked examples. |
| Periodic loading | Preserve the hourly loading requirement while joining released groups without blocking online work. Define how completed periodic results reach each hourly load. | Timestamped release, join, and fact-load evidence. |

## Schema and setup — 15 marks

| Done | Requirement | Implementation location | Acceptance evidence |
| --- | --- | --- | --- |
| [x] | Drop previous project tables and `STORE_QUARTERLY_SALES` safely before recreation. | `Create-DW/create_dw.sql` | User confirmed script runs successfully twice in Workbench. |
| [ ] | Customer, product, supplier, store, and time dimensions with original IDs, primary keys, and fact foreign keys. | `Create-DW/create_dw.sql` | Schema inspection and valid dimension references. |
| [ ] | One fact per transaction line with quantity, revenue, five dimension keys, invoice ID, sales mode, and processing mode. | Schema and `Mesh-Join/retail_dw/warehouse.py` | Line-level reconciliation, including repeated invoice products. |
| [ ] | Time columns include date, hour, day, month, quarter, year, day of week, weekday/weekend, season, and half-year using brief definitions. | Schema and warehouse loader | Boundary examples for weekends, seasons, quarters, and halves. |
| [ ] | Working tables hold disk-backed masters, periodic staging, and shed transactions with flags and shed time. | Schema and preparation | Table definitions and sample records. |

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
| [ ] | Calculate actual 500-row partition counts for both masters. | Preparation | Counts printed and included in report. |

## MESHJOIN and execution — 30 marks

| Done | Requirement | Implementation location | Acceptance evidence |
| --- | --- | --- | --- |
| [ ] | Prompt for host, port, database, username, password, and online file path. Never hardcode the password. | `main.py`, `database.py` | Fresh-start demonstration and source inspection. |
| [ ] | Print arrival mode, peak/non-peak rates, simulated-hour scale, and buffer limit at startup. | `main.py`, `settings.py` | Startup output. |
| [ ] | Three independent threads named OnlineThread, MeshJoinThread, and PeriodicThread. | `online.py`, `meshjoin.py`, `periodic.py` | Concurrent arrival and processing demonstration. |
| [ ] | Stream buffer limit 1,000; chunk size up to 500; disk pages up to 500. | `meshjoin.py`, settings | Observed limits and partial-final-chunk check. |
| [ ] | Multimap H contains actual transactions; queue contains references to chunks in arrival order. | `meshjoin.py` | Repeated join-key example and queue inspection. |
| [ ] | Read one page of each master per iteration, scan cyclically, and retain chunks for full coverage of both masters. Avoid full master dictionaries and per-transaction master queries. | `meshjoin.py` | Unequal master page counts and final-page match checks; database access review. |
| [ ] | Enrich each transaction from both masters, preserving transactional fields and flags. | `meshjoin.py` | Expected customer/product/supplier matches. |
| [ ] | Default online rates 80 peak and 10 non-peak; command-line rate control and runtime changes supported. | `online.py`, settings, commands | Measured rate change and status output. |
| [ ] | Release one periodic group each simulated hour; default 60 seconds; group 0 may release immediately. No duplicate group extraction. | `periodic.py` | Timestamped 48-group release record. |
| [ ] | Online first, periodic second, shed replay last. Shed only cleaned periodic rows under actual overload. | `meshjoin.py` | Peak pressure evidence, persisted shed rows, and priority observation. |
| [ ] | Recover shed rows during available non-peak capacity without loss or duplication. | `meshjoin.py`, database | Shed backlog drains and line identities reconcile. |
| [ ] | Compute revenue only after enrichment; insert dimensions before facts and preserve required modes. | `warehouse.py` | Decimal totals and foreign-key checks. |
| [ ] | Support online, periodic, both, peak, non-peak, status, and quit while running. | `commands.py`, `main.py` | Each command demonstrated with resulting status. |
| [ ] | Status prints mode, speed, buffer count, H count, and shed disk count. | `commands.py` | Output matches actual state. |
| [ ] | Shutdown closes threads/connections consistently; retries and replay cannot duplicate completed sales. | Runtime and loader | Shutdown and retry checks. |
| [ ] | Full required run loads every cleaned source line exactly once. | End-to-end run | Identity, count, quantity, and revenue reconciliation by channel; empty pending queues. |

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
