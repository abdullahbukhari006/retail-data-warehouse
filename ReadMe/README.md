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

The check driver fills and drains the stream to exercise the algorithm. Independent producer/worker threads, runtime commands, priority and shedding, and DW loading are later stages.

## Run MESHJOIN regression checks

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s .\Mesh-Join\tests -v
```

These tests create disposable disk-backed SQLite fixtures and adapt the cursor parameter syntax to exercise the same page reader and join. They require no MySQL credentials. They cover unequal scan lengths, partial last pages and chunks, repeated join keys, shared queue/hash references, buffer capacity, missing matches, output retries, and partial read failure handling. A full 397,884-row verification using disk-backed fixture masters also passed during development. Authenticated MySQL check results are recorded in the requirements checklist as they are confirmed.

## Settings and help

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py --help
```

Startup prints the arrival mode, peak rate (80 transactions/second), non-peak rate (10 transactions/second), simulated-hour length (60 seconds), and stream-buffer limit (1,000 transactions). The arguments `--peak-rate`, `--non-peak-rate`, `--seconds-per-hour`, and `--arrival-mode` configure the displayed runtime settings. Rates and hour length must be positive integers. These settings will drive processing when the worker threads are implemented.

The currently available commands are `check-db`, `clean`, `generate-data`, `load-masters`, `prepare-periodic`, `load-periodic`, and `check-join`. Runtime processing commands, the three threads, DW loading, analytical queries, and the cache bonus are subsequent development stages. Their operating instructions will be added with those features.

Exit status is 0 on success, 1 on connection/configuration failure, 2 for invalid command arguments, and 130 when interactive startup is cancelled.
