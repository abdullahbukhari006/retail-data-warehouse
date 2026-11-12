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

## Settings and help

```powershell
& .\.venv\Scripts\python.exe .\Mesh-Join\main.py --help
```

Startup prints the arrival mode, peak rate (80 transactions/second), non-peak rate (10 transactions/second), simulated-hour length (60 seconds), and stream-buffer limit (1,000 transactions). The arguments `--peak-rate`, `--non-peak-rate`, `--seconds-per-hour`, and `--arrival-mode` configure the displayed runtime settings. Rates and hour length must be positive integers. These settings will drive processing when the worker threads are implemented.

The currently available commands are `check-db` and `clean`. Master-data generation, channel assignment, periodic grouping, processing commands, the three threads, analytical queries, and the cache bonus are subsequent development stages. Their operating instructions will be added with those features.

Exit status is 0 on success, 1 on connection/configuration failure, 2 for invalid command arguments, and 130 when interactive startup is cancelled.
