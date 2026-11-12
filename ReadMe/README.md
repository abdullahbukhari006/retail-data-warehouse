# Running the Python application

Requires Python 3.10+ and MySQL 8.0.16+. Run the commands below in PowerShell from the repository root.

## Install dependencies

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\Mesh-Join\requirements.txt
```

The virtual environment stays local and is excluded from Git. Passwords are entered through a hidden prompt and are held only in memory.

## Check the existing database

First run `Create-DW/create_dw.sql` in MySQL Workbench against your project database. That script recreates the project tables and removes their existing data.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-db
```

Enter the host, port, database name, username, and password. Defaults are offered for host, port, and database. Successful output identifies the MySQL version and selected database and confirms all 10 required tables are present. The check reads database metadata and closes the connection.

Connection fields can also be supplied as arguments. The password is always prompted.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-db --host 127.0.0.1 --port 3306 --database retail_data_warehouse --user root
```

An optional `--online-file "Data/online_sales_stream.csv"` checks that the supplied file exists. It can be used after preparation generates the CSV. The database check can run before that file exists.

## Clean the original workbook

Place the original workbook at `Data/Online Retail.xlsx`, then run the following from the repository root. This command reads the workbook directly and requires no database credentials.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py clean
```

For another input or output location, supply explicit paths.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py clean --source-file "Data/Online Retail.xlsx" --output-dir Data
```

The command retains a row when quantity and unit price are positive, the customer ID is present, and the invoice ID does not start with C (case-insensitive). It prints total rows before/after cleaning and overlapping counts for the four exclusion conditions. Missing descriptions and repeated valid lines are preserved.

`Data/cleaned_sales.csv` contains `source_line_id`, `transaction_id`, `customer_id`, `product_id`, `product_name`, `quantity`, `unit_price`, `event_time`, and `country`. The source-line ID is the original Excel row number, including the header as row 1. Output follows original source order. Prices are written as decimal text, and timestamps use `YYYY-MM-DD HH:MM:SS` for this dataset.

The original workbook remains the input to each cleaning run. The command atomically replaces the cleaned CSV after successful processing. If a run fails or is interrupted, any existing cleaned CSV is retained. Later preparation stages will read the cleaned data to generate the online stream, periodic groups, and master data.

Verified counts for the downloaded workbook are 541,909 before cleaning, 397,884 after cleaning, and 144,025 removed. The detailed source and inspection record is in `Data/data_notes.md`.

## Generate channels, stores, and master data

After cleaning, run the following. It uses `Data/cleaned_sales.csv` by default and requires no database credentials.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py generate-data
```

The optional arguments `--cleaned-file PATH` and `--output-dir DIRECTORY` select alternative locations. Generation stages these four files, reuses byte-identical existing files, and restores previous changed files if publication fails. Windows denied replacing existing production CSVs during verification, so regeneration with changed input may require resolving access to those destination files or selecting a fresh output directory.

| File | Contents |
| --- | --- |
| `online_sales_stream.csv` | The seven prescribed online transaction fields, in cleaned source order. |
| `customer_master_data.csv` | One customer per cleaned ID with all seven prescribed fields. |
| `product_master_data.csv` | One product per cleaned ID with all five prescribed fields. |
| `assigned_sales.csv` | Internal preparation file containing all cleaned fields plus `sales_mode` and `store_id`, preserving the original source-line ID for both channels. |

The invoice MD5 assigns one channel and store to the entire shopping cart. Customer country comes from its first cleaned transaction. Equal-length longest descriptions retain the first occurrence. Customer/product attributes use the exact assignment lists and hash suffixes. The five-store lookup is in `retail_dw/rules.py` and is used outside MESHJOIN. Supplier is an attribute of the generated product master. The preparation dictionaries are built from cleaned input and released when generation ends. Runtime MESHJOIN will read its masters from database pages.

The online CSV retains its required seven-column format. `assigned_sales.csv` retains source-line identity and store assignments for later periodic staging and online identity mapping. Generated master rows are ordered by ID for reproducibility.

For the supplied workbook, generation produces 278,740 online transactions, 119,144 periodic transactions, 4,338 customers, and 3,665 products. The actual channel split is approximate because the hash applies per invoice.

## Load the disk-backed master tables

Run the following, then enter the same MySQL credentials used for `check-db`.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py load-masters
```

The command reads the master CSVs from `--output-dir` (default `Data`). It replaces both master tables within one database transaction, using parameterized inserts in batches of 500. A failed load rolls back both replacements. It prints the loaded row counts and page counts. Perform this preparation before starting worker threads.

Verify in MySQL Workbench after a successful load.

```sql
USE retail_data_warehouse;

SELECT 'customer_master_data' AS master_table,
       COUNT(*) AS row_count, CEIL(COUNT(*) / 500.0) AS partitions
FROM customer_master_data
UNION ALL
SELECT 'product_master_data', COUNT(*), CEIL(COUNT(*) / 500.0)
FROM product_master_data;
```

Expected results are 4,338 customer rows (9 pages) and 3,665 product rows (8 pages). These actual counts determine the runtime scan lengths. Warehouse dimension and fact tables are populated later by the enrichment loader.

Generation and master loading share a local lock to prevent reading mixed generations. If a process is forcibly terminated and leaves `Data/.generation.lock`, confirm no preparation command is still running before removing that lock and rerunning the command.

## Prepare and load periodic staging

After `generate-data`, prepare the hourly groups.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py prepare-periodic
```

This reads `assigned_sales.csv` from `--output-dir` (default `Data`), selects Periodic transactions, and sorts them by event time with original source-line ID as the tie-breaker. It writes `periodic_sales_data.csv` in that directory. The sorted transactions are split into 48 consecutive, balanced release groups. For the supplied data, hours 0–7 each contain 2,483 rows, and hours 8–47 each contain 2,482 rows. These are simulated release groups.

The prepared fields match the staging table, including source-line ID, sales mode, processing mode, release hour, and released flag. `sales_mode` and `processing_mode` both start as `Periodic`. Here, processing mode records the original ingestion path and remains unchanged during future shedding/recovery. Every `released` value starts at 0.

Load staging with your database credentials.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py load-periodic
```

The loader replaces `periodic_sales_data` in one database transaction, inserts batches of up to 500, and checks all database group counts and release flags before committing. A failure rolls back the replacement. A successful load reports 119,144 rows, 48 groups, a minimum group size of 2,482, and a maximum of 2,483. This authenticated load has been verified on the user's MySQL database.

Run preparation/loading before starting worker threads. Repeating `load-periodic` resets staging for a new run, including all release flags. Later runtime processing will release one group per simulated hour, using the default 60-second scale.

Additional Workbench verification is available with the following queries.

```sql
SELECT release_hour, COUNT(*) AS transactions,
       MIN(event_time) AS first_event, MAX(event_time) AS last_event,
       SUM(released) AS released_rows
FROM periodic_sales_data
GROUP BY release_hour
ORDER BY release_hour;

SELECT COUNT(*) AS total_transactions,
       COUNT(DISTINCT release_hour) AS hourly_groups,
       SUM(released) AS released_rows
FROM periodic_sales_data;
```

The second result should be 119144, 48, and 0 immediately after loading.

## Check the core MESHJOIN

After loading both masters, run the following with your database credentials.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-join
```

The check reads the first 1,000 assigned transactions and enriches them using the actual database master pages. It verifies the customer/product matches and prints partition counts, iteration/page counts, and final component sizes. With the supplied data, expect 1,000 enriched transactions, 10 iterations, customer/product partition counts of 9/8, and zero transactions remaining in the buffer and H or chunks in the queue.

For a full join verification of this dataset, use the following.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-join --limit 397884
```

Expected output is 397,884 enriched transactions in 804 iterations, reading 804 customer pages and 804 product pages. The command performs read-only verification and closes its database connection after the stream and pending chunks drain. It uses `assigned_sales.csv` from `--output-dir`, default `Data`.

The core implementation is in `retail_dw/meshjoin.py`.

- The Stream buffer is a thread-safe queue limited to 1,000 incoming transactions.
- Each iteration reads one disk page from each master and admits up to 500 transactions as a chunk. Each Disk buffer contains up to 500 master tuples. Ordered primary-key scans wrap independently after each master's actual last page.
- Hash table H is a multimap indexed by customer and product join keys. Its buckets and the Queue reference the same pending transaction objects. Repeated join keys and repeated invoice/product lines retain their distinct source-line identities.
- The Queue stores new chunks at the front and retires the oldest from the end. Every chunk stays for `max(customer partitions, product partitions)` iterations, giving full coverage of both masters regardless of its starting scan positions. For this dataset that is 9 iterations, with at most 9 overlapping 500-line chunks in H during an iteration.
- Completed chunks carry the original immutable transaction plus customer and product attributes, including supplier fields. Store enrichment and revenue calculation belong to the consolidation stage.
- `processing_mode` records the original ingestion path. Online input defaults to `Live`, and periodic input defaults to `Periodic`. An existing supplied value is preserved by MESHJOIN.
- The output callback must complete synchronously and commit atomically or be safe to retry. A failed output handoff retains the chunk and retries before the next scan/admission. A disk-read or invalid-input failure stops the join with retained state so a partial scan cannot lead to premature eviction. Missing master matches raise an error after full coverage.

The check driver fills and drains the stream to exercise the algorithm. Use `check-threads` below to exercise the independent workers, priority, shedding, and recovery. Use `run` for durable warehouse loading. Interactive runtime commands follow in step 12.

## Verify the three independent threads

Run this short verification after loading the masters and periodic staging.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-threads --limit 100 --hours 2 --seconds-per-hour 2
```

Enter your MySQL credentials and accept the default online-file path, or provide a matching file using `--online-file PATH`. The file must correspond to `assigned_sales.csv` in `--output-dir`, so each repeated online line receives its original source ID.

For the supplied data, the short check submits and enriches 100 online and 4,966 periodic transactions across two release groups. It uses 10 online transactions/second by default and a two-second simulated hour as explicitly selected above. The authenticated MySQL run completed in 11.146 seconds and drained the Stream buffer, H, and Queue to zero.

- `OnlineThread` reads the online CSV independently and paces arrivals at the selected peak/non-peak rate. Defaults are 80/10 transactions per second. CLI rate overrides are available, and `RunControl.set_speed()` supports runtime switching for the later command interface.
- `PeriodicThread` owns a database connection and reads a whole unreleased hourly group using an ordered locking query. Group 0 can start immediately. Later extraction starts are separated by at least one simulated hour. A full buffer applies cancellable backpressure. The next group follows after its interval and successful completion of the current group.
- `MeshJoinThread` owns its master-page connection and a dedicated shed-table connection. It creates both disk-page readers in that thread and processes chunks continuously. Incoming producers continue running during page reads and join iterations.
- A periodic group is acknowledged only when its complete set of rows has passed the output callback. The `run` command commits periodic facts and release flags atomically. During `check-threads`, flag updates are rolled back after each verified group.
- Worker failures signal the coordinator, cancel the producers, roll back an incomplete periodic release, and close worker-owned connections. Ctrl+C stops further arrivals and drains accepted work through the verification sink before shutdown.

The verification sink validates enrichment and counts results. Warehouse persistence, hourly fact loading, and runtime commands follow in their planned stages. `--hours` selects 1–48 release groups, and `--limit` caps online rows for this command. The default simulated hour remains 60 seconds.

## Verify priority, load shedding, and recovery

Use the following high-arrival-rate stress check with your MySQL credentials.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-threads --limit 10000 --hours 1 --arrival-mode peak --peak-rate 1000000
```

The default peak/non-peak rates are 80/10. This check explicitly overrides the configured peak rate to 1,000,000 transactions/second, creating arrival pressure for the verification run. The default simulated hour is 60 seconds, and this command selects group 0 immediately. Rates are pacing ceilings, and the actual throughput also depends on file reading, join processing, and buffer capacity.

The priority buffer holds at most 1,000 transactions and selects waiting online rows first, fresh periodic rows second, and replayed shed rows last. It preserves arrival order within each priority. Priority applies at chunk admission, and admitted chunks retain their full master-scan coverage.

MESHJOIN checks pressure immediately before chunk admission, after disk reads. When peak mode is active, H has active work, and the stream buffer is full, it persists up to 500 waiting fresh periodic rows to `shed_table` before freeing their buffer slots. Persistence includes all transaction fields, original mode flags, source identity, and the database-generated shedding timestamp. The schema and store validation restrict shedding to periodic transactions. A failed disk write retains the buffered rows.

During non-peak operation, available buffer slots admit replay rows at the lowest priority. Rows stay on disk while queued or joining. Their IDs prevent duplicate replay while in flight. The store deletes acknowledged rows only after the output callback succeeds. The current verification callback validates enrichment and counts outputs; the durable warehouse callback is the next implementation stage.

For a finite peak verification, the coordinator automatically switches to non-peak after the selected online input completes and an eligible shed backlog exists. It then drains that backlog before finishing. The output records this automatic transition. Interactive `run` keeps the selected speed until an operator changes it. Use `non-peak` to recover shed backlog.

`PeriodicThread` recognizes existing shed rows by source ID and checks their original fields against staging. Those rows are included in the group's acknowledgement set and replayed once during the group's scheduled release. Rows for later groups retain their disk ownership until those groups are released. Counters distinguish rows moved to disk during this run, replay admissions, acknowledged disk rows, total disk backlog, and backlog eligible for the selected hours.

The authenticated stress run processed 10,000 online and 2,483 periodic rows in 1.249 seconds. It shed 1,785 periodic rows, replayed and acknowledged all 1,785 in non-peak mode, and finished with zero disk backlog and empty join components. Shed volume depends on the observed saturation during each run. Source-line identities and flags are preserved through persistence and replay.

To exercise the full input with the original peak rate and hour scale, use the following. Online pacing alone requires about 58 minutes, with additional processing time depending on the machine.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py check-threads --limit 278740 --hours 48 --arrival-mode peak
```

## Load the warehouse

After preparing the input files and loading both masters and periodic staging, start the three-thread warehouse loader.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py run --batch --arrival-mode peak
```

Enter the database credentials and online-file path. `run` reads all online rows by default and processes 48 periodic groups. The default hour is 60 seconds and peak rate is 80 transactions/second. Online pacing requires about 58 minutes for the full input, with additional processing time depending on the machine. `--limit` and `--hours` select smaller runs.

For the short loading check, use the following.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py run --batch --limit 100 --hours 1 --arrival-mode peak --seconds-per-hour 2
```

That authenticated run loaded 100 online and 2,483 periodic facts. Independent source calculations matched 2,583 total facts, quantity 32,492, and revenue 63,009.850000 GBP. Repeating the command inserted zero facts, verified the 100 existing online facts, and skipped the already-released periodic group. The quantity and revenue totals matched the first run.

An accelerated full loading command is available for demonstration.

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py run --batch --arrival-mode peak --peak-rate 1000000 --seconds-per-hour 1
```

The selected rates and time scale print at startup. Expected complete-dataset totals are 397,884 facts, quantity 5,167,812, and revenue 8,911,407.904000 GBP. The batch loader switches to non-peak after online input completes when shed rows need recovery.

### Consolidation and transaction boundaries

`retail_dw/warehouse.py` consolidates only results that have finished both master joins. It verifies the master IDs and invoice channel, computes revenue using decimal arithmetic, assigns the store from the prescribed invoice rule outside MESHJOIN, and derives all time attributes from the original event timestamp. Each original source line produces one fact. Equal invoice/product lines retain their separate source IDs.

Each joined chunk inserts missing customer, product, supplier, store, and time dimension rows before creating facts or prepared results. Unique business IDs resolve surrogate keys. Existing attributes and source facts are compared against the new values, so a conflicting retry fails instead of overwriting history. Identical retries reuse their existing records. Dimension lookups are bounded to the current chunk.

Online results commit to `fact_sales` as their chunks complete. Completed periodic results commit to the working table `periodic_join_results`, with dimension keys, stored revenue, original flags, and release hour. Once the entire released group has joined, PeriodicThread publishes that hourly batch to `fact_sales`, deletes its working results, and marks its staging rows released in one transaction. The next extraction interval starts after retrieval of the preceding group, with acknowledgement/loading delays allowed to extend the interval. The working table contains individual prepared lines and is emptied by successful hourly publication.

The working table is included in the updated `Create-DW/create_dw.sql`. Existing installations receive it automatically when `run` starts, using the additive `Create-DW/add_periodic_join_results.sql` migration. This adds one ETL working table to the ten base tables. `check-db` verifies the original ten required tables.

Warehouse writes use READ COMMITTED transactions on worker-owned connections. Selected duplicate-key and lock/deadlock errors retry the complete chunk up to three times. Failed chunks roll back their dimension and output changes. A failed hourly publication rolls back facts, prepared-result cleanup, and release flags together. Completed working results survive an interruption and can be reused when the input is replayed.

Ctrl+C stops new arrivals and drains accepted join work. Periodic results belonging to an incomplete group stay in the working table with staging unreleased. Repeating `run` reuses matching prepared results/facts and completes the group. The final summary distinguishes newly inserted facts from existing ones and shows pending periodic results.

The baseline loading mode inserts online results directly into the warehouse. The optional online cache will be implemented after the required analyses and report are complete.

### Verify the database

Run these queries in Workbench after loading.

```sql
SELECT sales_mode, COUNT(*) AS fact_rows,
       SUM(quantity) AS quantity, SUM(revenue) AS revenue
FROM fact_sales
GROUP BY sales_mode;

SELECT COUNT(*) AS facts, COUNT(DISTINCT source_line_id) AS unique_source_lines,
       SUM(quantity) AS quantity, SUM(revenue) AS revenue
FROM fact_sales;

SELECT COUNT(*) AS prepared_periodic_rows FROM periodic_join_results;
SELECT COUNT(*) AS shed_rows FROM shed_table;
SELECT release_hour, COUNT(*) AS transactions, SUM(released) AS released_rows
FROM periodic_sales_data GROUP BY release_hour ORDER BY release_hour;
```

A complete run has 278,740 Online and 119,144 Periodic facts, equal fact/source-ID counts, zero prepared/shed rows, and every periodic staging row released. Reports and OLAP queries use the stored `revenue` column.

## Run regression checks

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s .\Mesh-Join\tests -v
```

These tests create disposable disk-backed SQLite fixtures and adapt cursor syntax to exercise the page reader, join, worker coordination, and warehouse loader. They require no MySQL credentials. The 50 tests cover unequal scans, partial pages/chunks, repeated keys, shared references, buffer limits, join/output failures, independent arrivals, hourly timing, connection ownership, rate changes, cancellation, priority, shedding/replay/restart, decimal calculations, calendar boundaries, fact identity, atomic hourly publication, retries, and repeated warehouse runs.

A full three-thread fixture run also enriched all 278,740 online and 119,144 periodic transactions across 48 groups in 47.522 seconds, using a one-second hour and a custom online rate of 1,000,000 transactions/second for accelerated verification. It checked every release interval, closed both worker connections, and confirmed all staging flags were rolled back. The authenticated two-group MySQL run used the default non-peak rate of 10 transactions/second. These results are recorded in the requirements checklist.

## Settings and help

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py --help
```

Startup prints the arrival mode, peak rate (80 transactions/second), non-peak rate (10 transactions/second), simulated-hour length (60 seconds), and stream-buffer limit (1,000 transactions). The arguments `--peak-rate`, `--non-peak-rate`, `--seconds-per-hour`, and `--arrival-mode` configure runtime behavior. Rates and hour length must be positive integers. These settings control online pacing and periodic release intervals.

The currently available commands are `check-db`, `clean`, `generate-data`, `load-masters`, `prepare-periodic`, `load-periodic`, `check-join`, `check-threads`, `reconcile`, `check-queries`, and `run`. Interactive operator commands are described below. The 20 analytical queries are available in `Queries-DW/analysis_queries.sql`. The cache bonus is a subsequent development stage.

Exit status is 0 on success, 1 on connection/configuration failure, 2 for invalid command arguments, and 130 when interactive startup is cancelled.

## Verified complete MySQL load

The full accelerated run completed in 94.375 seconds with 397,884 facts, 5,167,812 units, and revenue 8,911,407.904000. All 48 hourly groups were visited. Previously committed facts were verified and reused. Final shed rows, stream buffer, hash transactions, queue chunks, and prepared periodic results were zero. The earlier 2,583-fact subset rerun inserted zero additional facts and preserved quantity and revenue. The complete disposable warehouse audit also matched every fact to its source and checked dimension attributes and hourly release intervals.

## Interactive operator commands

Start the warehouse and its console with:

```powershell
python .\Mesh-Join\main.py run
```

Enter the connection details, private password, and online-file path at the prompts. Initial input mode is `both`; `--mode online` or `--mode periodic` selects a different initial mode. Arrival speed initially follows `--arrival-mode` and defaults to non-peak. A completed selected input leaves the console available until `quit`. Completed input is not rewound by switching modes.

| Command | Effect |
| --- | --- |
| `online` | Pause future periodic extractions and enable online input. |
| `periodic` | Pause online input and enable hourly periodic groups. |
| `both` | Enable both channels; the buffer retains online priority. |
| `peak` | Select the current peak rate and allow periodic shedding under overload. |
| `non-peak` | Select the current non-peak rate and allow eligible shed recovery. |
| `peak 120` | Change the peak rate to 120/second and select peak. |
| `non-peak 20` | Change the non-peak rate to 20/second and select non-peak. |
| `status` | Print input mode, speed, both configured rates, buffer count, H transaction count, queue chunks, disk backlog, progress counts, and completion state. |
| `help` | Print accepted commands. |
| `quit` | Stop producers, switch recovery to non-peak, drain accepted work, and close worker connections. |

Every successful mode or speed command prints status. Invalid commands or rates leave settings unchanged. Status uses worker-published counters and the current buffer size, so fields may reflect adjacent instants during concurrent work. It performs no database work on the console thread.

A mode change controls future input. A periodic group already extracted continues its submission and hourly publication, and accepted transactions continue joining. Paused periodic scheduling retains its next group and resumes without skipping groups or emitting catch-up bursts. There is at least the configured hourly interval between extractions. The transaction's original sales and processing flags remain unchanged.

During an interactive peak run, use `non-peak` when shed work needs recovery. Interactive runs retain operator-selected speed. `quit` and Ctrl+C allow recovery while draining accepted work. A partly submitted periodic group retains its unreleased staging rows and durable prepared results for retry. An input EOF also requests graceful shutdown. A worker failure stops producers and reports the error; durable staging/prepared/shed records support retry.

For automatic finite execution, use `run --batch` with the same limits and rate options. `check-threads` remains a finite verification command. Batch and verification process both channels. `--mode` applies to interactive `run`.

### Short authenticated operator check

```powershell
python .\Mesh-Join\main.py run --limit 100 --hours 2 --seconds-per-hour 2 --mode periodic
```

Enter `status`, `online`, `peak 120`, `non-peak 20`, `periodic`, and `both`, checking the status printed after each switch. Wait for the completion message, then enter `status` and `quit`. On the already populated warehouse, matching facts are verified without adding duplicates. Expected final totals remain 397,884 facts, 5,167,812 units, and revenue 8,911,407.904000. Previously released groups contain no pending rows, so the disposable regression fixture supplies the verification of unreleased-group pause/resume behavior.

Step 12 verification passed all 39 regression tests, including paused online input, resume identity, periodic extraction gating, all command/rate transitions, live status values, invalid input, EOF shutdown, completion keeping the console open, and quit draining accepted work with closed worker connections. Authenticated MySQL operator verification passed in 41.079 seconds. Initial periodic mode kept online submissions at zero. Commands online, peak 120, non-peak 120, periodic, both, status, and quit reported the selected modes and rates. All 100 online rows were enriched and verified as existing facts; no new facts were inserted. The two selected periodic groups were already released, so no periodic rows were resubmitted. The console remained available after completion and quit returned cleanly. Final buffer, H, queue, shed backlog, and prepared periodic results were zero. Warehouse totals remained 397,884 facts, 5,167,812 units, and revenue 8,911,407.904000. Unreleased-group pause/resume and active-input shutdown are covered by the disposable regression tests.

## Full warehouse reconciliation and quality checks

Finish the complete warehouse run and close it with `quit` before auditing. Keep the generated files unchanged during verification. Run:

```powershell
python .\Mesh-Join\main.py reconcile
```

Enter the database connection details and password. This command uses `--output-dir` (default `Data`) for its local source files. It requires `cleaned_sales.csv`, `assigned_sales.csv`, `online_sales_stream.csv`, and both master CSVs. The existing staging table supplies the periodic rows. A partial warehouse is expected to fail this complete-dataset check.

The checker reconstructs expectations from the cleaned source and compares every assigned field in source order. It validates positive quantities/prices, required identifiers, cancellation exclusion, increasing source IDs, exact invoice channel/store rules, and online CSV identity/order. Customer attributes use the first cleaned country. Product descriptions use the longest cleaned description with first-occurrence ties and UNKNOWN fallback. These reconstructed attributes are checked against both master CSVs, both database masters, and the five warehouse dimensions.

Every fact is matched by original source-line ID and checked for invoice, customer, product, supplier, store, event time, quantity, exact decimal revenue, sales mode, and original processing mode. Missing, extra, or duplicate line IDs fail verification. All calendar dimension fields are checked independently of the loader's calendar function. Periodic staging is matched against the sorted source, balanced 48-group placement, original fields, and released flags. Shed and prepared-periodic tables must be empty. Counts, quantity, and decimal revenue must agree per channel and overall. Equal aggregate totals cannot conceal incorrect individual lines.

Expected records are held in a temporary SQLite database inside `Data`, removed on completion or failure. This needs free space on the data drive. MySQL data is read in pages of at most 500 rows inside one read-only repeatable-read consistent snapshot, then rolled back and closed. The command performs no MySQL inserts, updates, deletes, or repairs. It does not retain full master tables in memory.

The summary prints PASS or FAIL, expected/actual totals per channel, dimension counts, event-time coverage, and periodic group size range. Failures include category counts and up to five example IDs or field differences per category. Exit status is 0 for a clean audit and 1 for mismatches or an input/database error. Resolve the reported cause before rerunning; reconciliation never changes existing facts automatically.

For this dataset, complete expected totals are 397,884 facts, 5,167,812 units, and GBP 8,911,407.904000; channels contain 278,740 Online and 119,144 Periodic lines. There are 4,338 customers, 3,665 products, five suppliers, five stores, and 48 periodic groups of 2,482–2,483 rows. The raw workbook-to-cleaned-file verification is recorded in step 5; this command checks the generated source files and warehouse against that cleaned baseline.

Step 13 regression checks cover a 601-line complete fixture spanning multiple database pages, repeated invoice/product lines, offsetting revenue errors with equal grand totals, missing/extra and duplicate source identities, orphan dimension references, wrong flags/calendar/master attributes, wrong release groups, pending shed rows, malformed exports, read-only access, and temporary-file cleanup. Authenticated full MySQL reconciliation passed; measured results are recorded below.

The complete disposable warehouse passed the new reconciliation command against all 397,884 source lines. Online totals were 278,740 facts, 3,526,585 units, and revenue 6,075,186.073000. Periodic totals were 119,144 facts, 1,641,227 units, and revenue 2,836,221.831000. Combined totals were 5,167,812 units and revenue 8,911,407.904000. All 4,338 customer, 3,665 product, five supplier, five store, and 17,282 time dimension rows matched. Event-time coverage was 2010-12-01 08:26:00 through 2011-12-09 12:50:00. All 48 periodic groups matched the sorted source with sizes 2,482–2,483; released flags and empty shed/prepared tables passed. All 46 regression tests passed. Authenticated full MySQL reconciliation also passed. The user confirmed matching facts, quantities, and decimal revenue for both channels and the complete warehouse, all 25,295 dimension rows (4,338 customer, 3,665 product, five supplier, five store, and 17,282 time rows), source event-time coverage, all 48 group assignments and released flags, and empty durable backlogs. Step 13 is complete.

## Execute and verify the 20 OLAP analyses

Open `Queries-DW/analysis_queries.sql` in MySQL Workbench, select the populated schema, choose **Don't Limit** for result rows, and execute the entire script. Q1–Q20 produce separately labeled result sets. Q20 creates/replaces the required `STORE_QUARTERLY_SALES` view and reads it in store-name/year/quarter order. The script leaves fact and dimension data unchanged.

For automated complete-result verification:

```powershell
python .\Mesh-Join\main.py check-queries
```

Enter your private database credentials. Keep the warehouse stopped and the source files unchanged. This uses the generated inputs under `--output-dir` (default `Data`) and compares each query with independent source-derived answers. A clean run ends with `OLAP verification: PASS (20/20)` and exit status 0. A mismatch names the question and shows bounded missing/extra result examples; SQL errors name the question and return status 1. The command also prints measured Q19 spikes for later discussion in the report. Source calculations and the product-pair query may take a while.

Use `reconcile` first to verify all individual warehouse lines. `check-queries` checks analytical answers, including rankings, purchase averages, null/zero baselines, subtotal levels, and the required ordering. It creates/replaces the Q20 view, so the account needs that privilege as well as SELECT.

The selected year is 2011, with partial coverage through December 9. Q6 covers July–December 2011. Q19 includes all 374 calendar days from December 1, 2010 through December 9, 2011 in each product's daily average, including zero-sales dates. Query comments specify each date range. Full definitions, tie handling, purchase grain, percentage-change conventions, the regular-view interpretation, and verification methods are in `Queries-DW/verification_notes.md`.

Step 14 local verification: all 50 tests and all 20 complete-source fixture query comparisons passed. Measured row counts and Q19 examples are recorded in `Queries-DW/verification_notes.md`. Authenticated native MySQL verification is pending.
