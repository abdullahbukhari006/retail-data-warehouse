"""Prepare the 48 hourly periodic groups and load their staging table."""

import csv
from datetime import datetime
from pathlib import Path
import tempfile

from retail_dw.prepare_data import (
    GENERATED_COLUMNS, generated_data_lock, numeric_value, publish_generated_files,
)
from retail_dw.rules import sales_assignment
from retail_dw.settings import RuntimeSettings


PERIODIC_COLUMNS = (
    "source_line_id", "transaction_id", "customer_id", "product_id",
    "quantity", "unit_price", "event_time", "sales_mode", "processing_mode",
    "release_hour", "released",
)
PERIODIC_FILENAME = "periodic_sales_data.csv"
GROUP_COUNT = RuntimeSettings().periodic_groups


def prepare_periodic_data(data_dir):
    """Sort periodic source lines, balancing group sizes within one row."""
    data_dir = Path(data_dir).resolve()
    rows = []
    with generated_data_lock(data_dir):
        with (data_dir / "assigned_sales.csv").open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != list(GENERATED_COLUMNS["assigned_sales.csv"]):
                raise ValueError("Unexpected assigned_sales.csv columns. Run generate-data again.")
            previous_id = 1
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Malformed assigned transaction row.")
                source_id = int(row["source_line_id"])
                if source_id <= previous_id:
                    raise ValueError("Assigned source-line IDs must be in original workbook order.")
                previous_id = source_id
                mode, store_id = sales_assignment(row["transaction_id"])
                if row["sales_mode"] != mode or int(row["store_id"]) != store_id:
                    raise ValueError(f"Incorrect channel/store assignment for source line {source_id}.")
                if mode == "Online":
                    continue
                quantity = numeric_value(row["quantity"], "Quantity", source_id)
                price = numeric_value(row["unit_price"], "UnitPrice", source_id)
                if (quantity <= 0 or quantity != quantity.to_integral_value()
                        or price <= 0 or not row["customer_id"].strip()):
                    raise ValueError(f"Invalid cleaned periodic transaction at source line {source_id}.")
                event_time = datetime.fromisoformat(row["event_time"])
                if event_time.tzinfo is not None:
                    raise ValueError("Source event timestamps must be timezone-naive.")
                rows.append((event_time, source_id, row))
        rows.sort(key=lambda item: (item[0], item[1]))
        base_size, remainder = divmod(len(rows), GROUP_COUNT)
        sizes = [base_size + (hour < remainder) for hour in range(GROUP_COUNT)]
        with tempfile.TemporaryDirectory(prefix="generation_", dir=data_dir) as folder:
            staging_dir = Path(folder)
            with (staging_dir / PERIODIC_FILENAME).open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=PERIODIC_COLUMNS)
                writer.writeheader()
                position = 0
                for hour, size in enumerate(sizes):
                    for _ in range(size):
                        _, _, row = rows[position]
                        position += 1
                        transaction = {name: row[name] for name in PERIODIC_COLUMNS[:8]}
                        transaction.update(processing_mode="Periodic", release_hour=hour, released=0)
                        writer.writerow(transaction)
            publish_generated_files(staging_dir, data_dir, (PERIODIC_FILENAME,))
    return sizes


def load_periodic_data(config, data_dir):
    """Replace staging and verify the database contents before committing.

    This is a preparation command. It resets all release flags for a new run.
    It must be called before starting the processing threads.
    """
    from retail_dw.database import database_connection, database_transaction

    data_dir = Path(data_dir).resolve()
    expected_sizes = [0] * GROUP_COUNT
    previous_order = None
    previous_hour = 0
    seen_ids = set()
    with generated_data_lock(data_dir):
        with (data_dir / PERIODIC_FILENAME).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(PERIODIC_COLUMNS):
                raise ValueError("Unexpected periodic CSV columns. Run prepare-periodic again.")
            with database_connection(config) as connection, database_transaction(connection):
                cursor = connection.cursor()
                try:
                    cursor.execute("DELETE FROM periodic_sales_data")
                    statement = (
                        f"INSERT INTO periodic_sales_data ({', '.join(PERIODIC_COLUMNS)}) "
                        f"VALUES ({', '.join(['%s'] * len(PERIODIC_COLUMNS))})"
                    )
                    batch = []
                    for row in reader:
                        if None in row or any(value is None for value in row.values()):
                            raise ValueError("Malformed periodic CSV row.")
                        source_id = int(row["source_line_id"])
                        hour = int(row["release_hour"])
                        event_time = datetime.fromisoformat(row["event_time"])
                        order = (event_time, source_id)
                        if source_id < 2 or source_id in seen_ids:
                            raise ValueError("Periodic source-line IDs must be valid and unique.")
                        if not 0 <= hour < GROUP_COUNT:
                            raise ValueError("Periodic release hour must be between 0 and 47.")
                        if hour < previous_hour:
                            raise ValueError("Periodic release hours must follow event-time order.")
                        if event_time.tzinfo is not None:
                            raise ValueError("Periodic event timestamps must be timezone-naive.")
                        if previous_order is not None and order <= previous_order:
                            raise ValueError("Periodic CSV must be sorted by event time and source-line ID.")
                        if row["sales_mode"] != "Periodic" or sales_assignment(row["transaction_id"])[0] != "Periodic":
                            raise ValueError("Staging must contain only periodic transactions.")
                        if row["processing_mode"] != "Periodic" or row["released"] != "0":
                            raise ValueError("Prepared periodic rows must have their original mode and unreleased flag.")
                        quantity = numeric_value(row["quantity"], "Quantity", source_id)
                        price = numeric_value(row["unit_price"], "UnitPrice", source_id)
                        if (quantity <= 0 or quantity != quantity.to_integral_value() or price <= 0
                                or not all(row[name].strip() for name in ("transaction_id", "customer_id", "product_id"))
                                or row["transaction_id"].upper().startswith("C")):
                            raise ValueError(f"Invalid periodic transaction at source line {source_id}.")
                        previous_order = order
                        previous_hour = hour
                        seen_ids.add(source_id)
                        expected_sizes[hour] += 1
                        values = (
                            source_id, row["transaction_id"], row["customer_id"], row["product_id"],
                            int(quantity), price, event_time,
                            row["sales_mode"], row["processing_mode"], hour, 0,
                        )
                        batch.append(values)
                        if len(batch) == 500:
                            cursor.executemany(statement, batch)
                            batch.clear()
                    if batch:
                        cursor.executemany(statement, batch)
                    base_size, remainder = divmod(sum(expected_sizes), GROUP_COUNT)
                    if expected_sizes != [base_size + (hour < remainder) for hour in range(GROUP_COUNT)]:
                        raise ValueError("Periodic groups must follow the balanced 48-group partition.")
                    cursor.execute(
                        "SELECT release_hour, COUNT(*), SUM(released) "
                        "FROM periodic_sales_data GROUP BY release_hour ORDER BY release_hour"
                    )
                    actual_sizes = [0] * GROUP_COUNT
                    for hour, count, released in cursor.fetchall():
                        if not 0 <= hour < GROUP_COUNT or released != 0:
                            raise ValueError("Staging contains an invalid release hour or released row.")
                        actual_sizes[hour] = count
                    if actual_sizes != expected_sizes:
                        raise ValueError("Database staging group counts do not match the periodic input.")
                finally:
                    cursor.close()
    return actual_sizes
