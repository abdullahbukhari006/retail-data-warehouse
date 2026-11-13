# Retail data warehouse

**Abdullah Bukhari — 24I-2601 — DS-5A**

**Instructor:** Dr. Asif Naeem · DS3003 and DS3004 · Fall 2026

The project cleans the original UCI workbook, prepares deterministic customer/product masters and sales channels, joins Online and Periodic input through three independent threads, loads a five-dimension star, and answers Q1–Q20. The optional Online cache bonus is implemented. The default loading mode is `direct`; select `--loading-mode cache` for non-peak cache transfer.

## Package contents and prerequisites

Extract the ZIP and open PowerShell in the folder containing these six folders. All commands below run from that folder; no developer-specific path is required.

| Folder | Contents |
| --- | --- |
| `Create-DW` | Full MySQL schema and two additive working-table migrations. |
| `Mesh-Join` | Entry point, application modules, pinned dependencies, and 62 regression tests. |
| `Queries-DW` | Numbered Q1–Q20 SQL script and calculation/verification notes. |
| `Project-Report` | Matching eleven-page PDF and editable DOCX, plus permanent verification evidence. |
| `Data` | Unchanged original workbook, required exports, reproducibility inputs, and source notes. |
| `ReadMe` | This guide, final requirements review, and a SHA-256 payload manifest. |

Tested environment: **Python 3.13.3**, **MySQL 8.0.45**, `mysql-connector-python 9.7.0`, and `openpyxl 3.1.5`. The SQL needs MySQL 8.0.16+ for enforced CHECK constraints and MySQL 8 window/CTE/GROUPING support. MySQL must be running. Initial dependency installation needs internet access unless the packages are already cached; no dataset download is needed because the workbook is included. Workbench is used to execute SQL; no Office application is needed to run the Python project. SQLite used by disposable verification fixtures is part of Python and is not the production warehouse.

Allow space for the extracted data and temporary disk-based reconciliation/reference files. Use an account able to create the schema/tables/view for first setup, and SELECT/INSERT/UPDATE/DELETE for execution. Passwords are entered privately at the hidden prompt and are never embedded in source, configuration files, or commands.

## 1 Install the Python dependencies

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\Mesh-Join\requirements.txt
& .\.venv\Scripts\python.exe -m pip check
```

The examples invoke the environment directly, so PowerShell activation and execution-policy changes are unnecessary. On another operating system, use the equivalent environment Python executable, such as `.venv/bin/python`.

## 2 Create a new warehouse

In MySQL Workbench, open and execute **all of** `Create-DW/create_dw.sql`. Its bootstrap creates/selects `retail_data_warehouse`; edit those two statements if you choose another database name. The script drops the old project view/tables before recreation and **removes existing project data**. Run it only for a fresh/reset installation.

The schema has five dimensions, one line-level fact, two disk masters, periodic staging, shed storage, periodic prepared results, and the bonus Online cache: twelve tables in total. Q20 creates the view later.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-db
```

Enter host (default `127.0.0.1`), port (`3306`), database (`retail_data_warehouse`), username, and password. The command checks connectivity and the original ten required base tables. Credentials must match your server; a failed login does not change data.

**Existing populated database:** skip the full recreation and replacement preparation loads. Normal `run` startup applies `add_periodic_join_results.sql` and `add_online_sales_cache.sql` if their working tables are missing, preserving existing facts. Read-only reconciliation also accepts an absent optional cache on a pre-bonus direct installation.

## 3 Reproduce the prepared files from the original workbook

The original input is included at `Data/Online Retail.xlsx`. Cleaning and generation need no database credentials.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py clean
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py generate-data
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py prepare-periodic
```

| Stage | Expected result |
| --- | --- |
| Cleaning | 541,909 before, 397,884 after, 144,025 removed. |
| Channel assignment | 278,740 Online and 119,144 Periodic lines. |
| Masters | 4,338 customers and 3,665 products. |
| Periodic preparation | 48 sorted groups, smallest 2,482 and largest 2,483. |

The four overlapping exclusions are cancellations (9,288), nonpositive quantity (10,624), nonpositive price (2,517), and missing customer (135,080). Their sum is not the union removed. Valid repeated lines remain distinct and retain original Excel row numbers as `source_line_id`. The source workbook is unchanged.

The invoice's first eight UTF-8 MD5 hexadecimal digits, interpreted as h, assign Online for h % 10 in 0–6 and Periodic for 7–9. Online uses store 1; Periodic uses 2 + h % 4. Master demographics/categories/suppliers are deterministic assignment-generated attributes. Customer city category uses the first cleaned country. Product name uses the longest cleaned description, first occurrence on equal-length ties, or UNKNOWN. Full rules and evidence are in `Data/data_notes.md` and `retail_dw/rules.py`.

Required exports are `online_sales_stream.csv` (seven prescribed fields), `customer_master_data.csv` (seven fields), and `product_master_data.csv` (five fields). Supporting `cleaned_sales.csv` and `assigned_sales.csv` preserve source-line identity, country, and store/channel assignments. `periodic_sales_data.csv` carries sorted hourly groups and initial release flags. These supporting files are included because runtime/reconciliation require them. Generation reuses identical outputs and publishes changed outputs atomically. Preparation commands use a local `.generation.lock`; after a forced termination, remove a leftover lock only after confirming no preparation process is running.

For alternative locations, use `clean --source-file PATH --output-dir DIRECTORY`, `generate-data --cleaned-file PATH --output-dir DIRECTORY`, then the same `--output-dir` on subsequent preparation/loading/verification commands. If an output file is open or protected, close it or use a writable new output directory.

## 4 Load masters and periodic staging

On the newly created warehouse, run:

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py load-masters
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py load-periodic
```

Both prompt for database credentials. Master loading commits the customer/product replacements together, in batches up to 500. Actual scan sizes are 9 customer and 8 product partitions. Periodic loading replaces staging and verifies 119,144 rows across release hours 0–47, all initially unreleased. Groups 0–7 contain 2,483 rows; groups 8–47 contain 2,482. It sorts by event time and source-line ID.

Preparation/loading must finish before worker threads start. **Repeating `load-periodic` resets release flags.** It is a preparation/reset operation, not the resume command. Normal `run` reuses committed facts and staged progress.

## 5 Start processing

Interactive operation with the required defaults:

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py run
```

Enter the database details and online-file path; pressing Enter accepts `Data/online_sales_stream.csv`. Startup prints arrival speed, peak 80/second, non-peak 10/second, 60 seconds per simulated hour, 1,000-row buffer limit, and selected loading mode. Initial input mode is `both`; initial speed is non-peak. The complete Online file is read unless `--limit` is supplied. Periodic defaults to all 48 groups.

At 10/second, Online pacing alone takes approximately 7.7 hours; at 80/second it takes about 58 minutes. Processing adds time. To select peak at startup, add `--arrival-mode peak`. For a finite accelerated demonstration that prints its overridden settings:

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py run --batch --arrival-mode peak --peak-rate 1000000 --seconds-per-hour 1
```

This still processes every Online line and all 48 groups. Rates are pacing ceilings, not guaranteed achieved throughput. `--batch` automatically enters non-peak after Online completion when shed/cache backlog needs recovery and exits after draining. Interactive execution keeps accepting commands after completion until `quit`; use `non-peak` to recover durable backlog.

### Interactive commands

| Command | Result |
| --- | --- |
| `online` | Enable Online input and pause future Periodic extractions. |
| `periodic` | Enable Periodic groups and pause the Online reader. |
| `both` | Enable both channels, retaining Online priority. |
| `peak` or `peak 120` | Select peak speed, optionally changing its positive rate. |
| `non-peak` or `non-peak 20` | Select non-peak speed, optionally changing its positive rate. |
| `status` | Print mode, speed/rates, buffer, H, queue, shed count, cache count, loading mode, and progress. |
| `help` | Print accepted commands. |
| `quit` | Stop input, enter non-peak, drain accepted work, and close connections. |

A mode change controls new input. An already extracted Periodic group and accepted transactions continue processing. Paused schedules resume without skipping groups or catch-up bursts. Group 0 can release immediately; every later extraction remains at least the configured hour apart. Completed files are not rewound by switching modes. Status reflects the latest worker updates, so concurrent fields can describe adjacent instants.

Ctrl+C also stops input and drains accepted work. On failure, durable staging/prepared/shed/cache rows remain available. Rerun `run` with matching files and credentials to resume. Existing matching facts are verified; conflicts stop execution rather than silently overwrite a sale. Incomplete Periodic results retain unreleased staging until a successful complete-group commit.

### Runtime settings

| Argument | Default and meaning |
| --- | --- |
| `--arrival-mode peak|non-peak` | `non-peak` initial speed. |
| `--peak-rate N`, `--non-peak-rate N` | 80 and 10; positive integers. |
| `--seconds-per-hour N` | 60 real seconds; positive integer, printed at startup. |
| `--hours N` | 48 groups, range 1–48. |
| `--limit N` | All Online rows for `run`, default 1,000 for verification. |
| `--mode online|periodic|both` | `both`, interactive `run` only. |
| `--loading-mode direct|cache` | `direct`; bonus applies to `run` only. |
| `--batch` | Finite non-interactive `run`, both channels. |
| `--online-file PATH` | Supply Online path instead of prompting. |
| `--output-dir DIRECTORY` | `Data`, prepared/source verification inputs. |
| `--host`, `--port`, `--database`, `--user` | Supply connection fields instead of prompts; password still prompted. |

The fixed assignment sizes are master page 500, stream chunk 500, buffer 1,000, and 48 Periodic groups. The worker implementation reads masters cyclically from disk and never replaces them with a whole-master lookup cache. The stream priority is Online, fresh Periodic, then shed replay. Only cleaned Periodic rows under actual peak overload may be shed.

## 6 Enable and demonstrate the bonus cache

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py run --loading-mode cache
```

Cache mode writes completed Online/Live results to `online_sales_cache` after both matches, consolidation, and dimension resolution. Peak speed holds them on disk. Non-peak transfers at most 500 rows per pass, verifies/inserts their facts, and deletes those cache rows in the **same transaction**. Failures roll back the entire transfer. Original IDs and flags are preserved. Periodic results never enter this cache and still publish hourly through `periodic_join_results`.

A peak command takes effect after an atomic batch already in progress; no new cache transfer starts afterward. Matching retries pass through cache and verify the existing fact before deletion. Prior durable cache rows recover even after returning to direct mode. Final output distinguishes newly inserted facts, newly cached rows, transferred rows, and matching facts already present. The high-water value is the highest observed backlog checkpoint.

Short interactive demonstration:

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py run --loading-mode cache --mode online --arrival-mode peak --limit 100 --hours 1 --seconds-per-hour 2
```

Use `status` until Online shows 100/100 and cache shows 100. Enter `non-peak`, then `both`, wait for completion, then `status` and `quit`. On the already loaded warehouse these are matching retries, so 100 rows transfer/remove and zero new facts are added. Fresh-row deferral and peak Periodic publication are independently tested in disposable fixtures.

Full finite bonus run:

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py run --batch --loading-mode cache --arrival-mode peak --peak-rate 1000000 --seconds-per-hour 1
```

The full-source fixture deferred and transferred 278,740 new Online facts, retained 119,144 Periodic facts, and matched all 397,884 final fact payloads against direct loading. Native MySQL also demonstrated peak cache 100 followed by non-peak cache zero with unchanged complete totals.

## 7 Reconcile the complete warehouse

After the complete run finishes and closes, keep generated files unchanged and run:

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py reconcile
```

The read-only checker compares every source-line identity and fact field, all five dimensions and both masters, required rules/calendar fields, per-channel totals, sorted group assignments, released flags, and empty shed/prepared/cache backlogs. It reconstructs expectations on temporary disk storage under `Data`, uses MySQL 500-row pages in a consistent read-only snapshot, and removes temporary files afterward. It reports bounded mismatch examples and never repairs data automatically. An intentionally partial run is expected to fail this complete-dataset check.

| Channel | Facts | Quantity | Revenue GBP |
| --- | ---: | ---: | ---: |
| Online | 278,740 | 3,526,585 | 6,075,186.073000 |
| Periodic | 119,144 | 1,641,227 | 2,836,221.831000 |
| Total | 397,884 | 5,167,812 | 8,911,407.904000 |

Expected dimensions are 4,338 customers, 3,665 products, 5 suppliers, 5 stores, and 17,282 time rows. All 48 group assignments and release flags must match; all durable backlogs must be zero. Valid repeated lines remain separate facts under unique `source_line_id`.

## 8 Execute and verify Q1–Q20

Open `Queries-DW/analysis_queries.sql` in Workbench, select the populated schema, choose **Don't Limit** for result rows, and execute the complete script. Q1 and Q11 have separately labeled answers. Q20 creates/replaces `STORE_QUARTERLY_SALES` and reads it ordered by store name, year, and quarter. Other queries leave warehouse data unchanged.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-queries
```

This executes every delivered query and compares every result row with separately calculated source answers. The reference calculations and product-pair query can take time. It requires SELECT plus permission to create/replace the Q20 view and returns `OLAP verification: PASS (20/20)` for complete success. Q1–Q19 use a read-only snapshot; it closes before Q20 DDL. Mismatches name the question and show bounded examples. Five relative Q19 spike examples are printed for interpretation.

The selected year is 2011 through December 9. Q6 uses July–December 2011, including the partial last month. Q19 includes all 374 calendar days from December 1, 2010 through December 9, 2011 in its average, including zero-sales dates. Ranking ties, cart-level average purchase grain, zero baselines, partial periods, Q13 exact-byte name grouping, and the regular-view interpretation of Q20 are documented in SQL comments and `Queries-DW/verification_notes.md`.

## 9 Component and regression checks

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-join --limit 1000
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-threads --limit 100 --hours 2 --seconds-per-hour 2
& .\.venv\Scripts\python.exe -m unittest discover -s .\Mesh-Join\tests -v
```

`check-join` verifies the paged join without writing facts. `check-threads` exercises independent workers, priority, shedding, and replay with a validation sink and rolls back release flags. Shed storage is genuinely exercised and can contain recoverable work after an interrupted check, so close normal runs before verification. The 62 regression tests use temporary disk-backed SQLite fixtures, need no credentials, and cover concurrency, timing, failure/restart, exact facts, all analyses, and the bonus. Test-only adapters handle MySQL dialect differences; native MySQL results are separately recorded in `Project-Report/evidence/verification_record.md`.

If the operating-system temporary folder is restricted, use a writable project-local test folder in this PowerShell session:

```powershell
New-Item -ItemType Directory -Force .\tmp\test-temp | Out-Null
$env:TEMP = (Resolve-Path .\tmp\test-temp).Path
$env:TMP = $env:TEMP
& .\.venv\Scripts\python.exe -m unittest discover -s .\Mesh-Join\tests -v
```

For available options use `python Mesh-Join/main.py --help` with the environment Python. Success is exit 0; a connection/configuration/verification failure is 1; invalid parser arguments are 2; interactive cancellation is 130. Review mismatches before retrying. No database dump or private credentials are needed in the submission.

## Report and permanent verification

`Project-Report/Project_Report.pdf` is the eleven-page reading/submission report and `Project_Report.docx` is the matching editable copy. It includes the implemented star, MESHJOIN, three shortcomings, cleaning and channel rules, measured shed/replay results, all analyses, Q19 findings, reflection, citation, and the verified bonus. Cover details are Abdullah Bukhari, 24I-2601, DS-5A, and Dr. Asif Naeem.

`ReadMe/final_requirements_review.md` records the final package review, including fresh-extraction checks. `ReadMe/submission_manifest.json` lists SHA-256 hashes and sizes of every other payload file. Its own hash is excluded to avoid self-reference. The ZIP has a separate SHA-256 sidecar outside the archive. The development checklist is not part of the final submission; permanent evidence remains in the report folder.

## Dataset attribution and provenance

**Required citation:** Chen, D., Sain, S. L., and Guo, K. Online Retail. UCI Machine Learning Repository. https://archive.ics.uci.edu/dataset/352/online+retail. **Downloaded 12 November 2026**, Asia/Karachi.

The unchanged workbook is included for reproducibility. Its SHA-256 is `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d`. UCI license metadata is CC BY 4.0. Slow UCI transfers led to acquisition from the public mirror https://raw.githubusercontent.com/pplonski/datasets-for-start/master/online-retail/Online-Retail.xlsx. The 2,134,016-byte UCI prefix matched, but full equality against a complete UCI download was not established. Detailed provenance, counts, and limitations are retained in `Data/data_notes.md`. Generated demographics, categories, channels, suppliers, and stores follow assignment rules and are not measured commercial attributes.

## Submission

Submit `AbdullahBukhari_24I-2601_Project.zip` to Google Classroom by **13 November 2026 at 23:00**. It contains the six required folders at archive root, the three required CSV exports plus reproducibility inputs, both report formats, and operating/verification instructions. Virtual environments, Git data, credentials, temporary databases/caches, bytecode, and the development checklist are excluded. Uploading and verifying the Classroom receipt remain the student's final action.
