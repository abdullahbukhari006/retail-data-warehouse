"""Read the original workbook and prepare cleaned transaction lines."""

import csv
import filecmp
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tempfile
from contextlib import ExitStack, contextmanager
import shutil

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from zipfile import BadZipFile

from retail_dw.rules import customer_attributes, product_attributes, sales_assignment


SOURCE_COLUMNS = (
    "InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
    "UnitPrice", "CustomerID", "Country",
)
CLEANED_COLUMNS = (
    "source_line_id", "transaction_id", "customer_id", "product_id",
    "product_name", "quantity", "unit_price", "event_time", "country",
)
ONLINE_COLUMNS = (
    "transaction_id", "customer_id", "product_id", "quantity",
    "unit_price", "event_time", "sales_mode",
)
CUSTOMER_COLUMNS = (
    "customer_id", "gender", "age_group", "occupation", "city_category",
    "stay_in_current_city_years", "marital_status",
)
PRODUCT_COLUMNS = (
    "product_id", "product_name", "product_category", "supplier_id", "supplier_name",
)
GENERATED_COLUMNS = {
    "assigned_sales.csv": (*CLEANED_COLUMNS, "sales_mode", "store_id"),
    "online_sales_stream.csv": ONLINE_COLUMNS,
    "customer_master_data.csv": CUSTOMER_COLUMNS,
    "product_master_data.csv": PRODUCT_COLUMNS,
}


@dataclass
class CleaningCounts:
    before: int = 0
    after: int = 0
    cancellations: int = 0
    nonpositive_quantity: int = 0
    nonpositive_price: int = 0
    missing_customer: int = 0

    @property
    def removed(self):
        return self.before - self.after

    def print_summary(self):
        print(f"Transactions before cleaning: {self.before:,}")
        print(f"Transactions after cleaning: {self.after:,}")
        print(f"Transactions removed: {self.removed:,}")
        print("Overlapping exclusion counts (a row can fail several tests)")
        print(f"  Cancellations: {self.cancellations:,}")
        print(f"  Nonpositive quantity: {self.nonpositive_quantity:,}")
        print(f"  Nonpositive unit price: {self.nonpositive_price:,}")
        print(f"  Missing customer ID: {self.missing_customer:,}")


def identifier_text(value):
    """Keep text identifiers and avoid a .0 suffix on integral numeric IDs."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def numeric_value(value, field, source_line_id):
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(
            f"Workbook row {source_line_id}: {field} must be numeric."
        ) from error
    if not number.is_finite():
        raise ValueError(f"Workbook row {source_line_id}: {field} must be finite.")
    return number


def clean_transactions(source_file, output_dir):
    """Write each valid source line once, preserving workbook order and identity.

    The output is replaced only after the entire workbook has been read
    successfully. A failed or interrupted run retains any previous output.
    No database connection is required by this stage.
    """
    source_file = Path(source_file).resolve()
    output_dir = Path(output_dir).resolve()
    output_file = output_dir / "cleaned_sales.csv"
    if not source_file.is_file():
        raise ValueError(f"Source workbook does not exist: {source_file}")
    if source_file == output_file:
        raise ValueError("Source workbook and cleaned output must have different paths.")

    try:
        workbook = load_workbook(source_file, read_only=True, data_only=True)
    except (InvalidFileException, BadZipFile) as error:
        raise ValueError("Source file must be a valid Excel .xlsx workbook.") from error

    temporary_path = None
    counts = CleaningCounts()
    try:
        if "Online Retail" not in workbook.sheetnames:
            raise ValueError("Source workbook must contain the 'Online Retail' worksheet.")
        rows = workbook["Online Retail"].iter_rows(values_only=True)
        if next(rows, None) != SOURCE_COLUMNS:
            raise ValueError("Source worksheet columns do not match the UCI Online Retail workbook.")
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".tmp",
            prefix="cleaned_sales_", dir=output_dir, delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            writer = csv.writer(temporary)
            writer.writerow(CLEANED_COLUMNS)
            for source_line_id, row in enumerate(rows, start=2):
                counts.before += 1
                invoice, product, description, quantity, event_time, price, customer, country = row
                transaction_id = identifier_text(invoice)
                customer_id = identifier_text(customer)
                quantity = numeric_value(quantity, "Quantity", source_line_id)
                price = numeric_value(price, "UnitPrice", source_line_id)
                cancelled = transaction_id.upper().startswith("C")
                nonpositive_quantity = quantity <= 0
                nonpositive_price = price <= 0
                missing_customer = not customer_id
                counts.cancellations += cancelled
                counts.nonpositive_quantity += nonpositive_quantity
                counts.nonpositive_price += nonpositive_price
                counts.missing_customer += missing_customer
                if cancelled or nonpositive_quantity or nonpositive_price or missing_customer:
                    continue
                if quantity != quantity.to_integral_value():
                    raise ValueError(f"Workbook row {source_line_id}: Quantity must be an integer.")
                if not isinstance(event_time, datetime):
                    raise ValueError(f"Workbook row {source_line_id}: InvoiceDate must be a datetime.")
                writer.writerow((
                    source_line_id, transaction_id, customer_id,
                    identifier_text(product),
                    "" if description is None else str(description),
                    int(quantity), format(price, "f"),
                    event_time.isoformat(sep=" "),
                    "" if country is None else str(country),
                ))
                counts.after += 1
        temporary_path.replace(output_file)
        return counts, output_file
    finally:
        workbook.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def publish_generated_files(staging_dir, output_dir, names=GENERATED_COLUMNS):
    """Restore previous files if publishing any part of the new set fails."""
    backup_dir = staging_dir / "previous"
    backup_dir.mkdir()
    changed = [
        name for name in names
        if not (output_dir / name).is_file()
        or not filecmp.cmp(staging_dir / name, output_dir / name, shallow=False)
    ]
    for name in changed:
        destination = output_dir / name
        if destination.exists():
            shutil.copy2(destination, backup_dir / name)
    published = []
    try:
        for name in changed:
            (staging_dir / name).replace(output_dir / name)
            published.append(name)
    except BaseException:
        for name in reversed(published):
            backup = backup_dir / name
            if backup.exists():
                backup.replace(output_dir / name)
            else:
                (output_dir / name).unlink()
        raise


@contextmanager
def generated_data_lock(output_dir):
    """Keep generation and master loading from observing mixed file sets."""
    lock_path = output_dir / ".generation.lock"
    try:
        lock = lock_path.open("x")
    except FileExistsError as error:
        raise ValueError(
            "Generated data is already locked. If a previous process was terminated, "
            f"confirm it has stopped before removing {lock_path}."
        ) from error
    try:
        with lock:
            yield
    finally:
        lock_path.unlink(missing_ok=True)


def generate_sales_data(cleaned_file, output_dir):
    """Generate file outputs from cleaned source order, before runtime joins."""
    cleaned_file = Path(cleaned_file).resolve()
    output_dir = Path(output_dir).resolve()
    if not cleaned_file.is_file():
        raise ValueError(f"Cleaned input does not exist: {cleaned_file}")
    if cleaned_file in {output_dir / name for name in GENERATED_COLUMNS}:
        raise ValueError("Cleaned input must have a different path from generated outputs.")
    output_dir.mkdir(parents=True, exist_ok=True)
    with generated_data_lock(output_dir):
        with tempfile.TemporaryDirectory(prefix="generation_", dir=output_dir) as folder:
            staging_dir = Path(folder)
            customers = {}
            products = {}
            counts = {"total": 0, "online": 0, "periodic": 0}
            store_counts = {store_id: 0 for store_id in range(1, 6)}
            with ExitStack() as stack:
                source = stack.enter_context(cleaned_file.open(encoding="utf-8", newline=""))
                reader = csv.DictReader(source)
                if reader.fieldnames != list(CLEANED_COLUMNS):
                    raise ValueError("Cleaned CSV columns do not match the clean command output.")
                writers = {}
                for name, columns in GENERATED_COLUMNS.items():
                    handle = stack.enter_context((staging_dir / name).open("w", encoding="utf-8", newline=""))
                    writers[name] = csv.DictWriter(handle, fieldnames=columns)
                    writers[name].writeheader()
                previous_id = 1
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("Malformed cleaned CSV row.")
                    source_id = int(row["source_line_id"])
                    if source_id <= previous_id:
                        raise ValueError("Cleaned source-line IDs must be strictly increasing in workbook order.")
                    previous_id = source_id
                    quantity = numeric_value(row["quantity"], "Quantity", source_id)
                    price = numeric_value(row["unit_price"], "UnitPrice", source_id)
                    if (quantity <= 0 or quantity != quantity.to_integral_value()
                            or price <= 0 or not row["customer_id"].strip()
                            or row["transaction_id"].upper().startswith("C")):
                        raise ValueError(f"Source line {source_id} fails the cleaning rules. Run clean again.")
                    if not row["transaction_id"] or not row["product_id"]:
                        raise ValueError(f"Source line {source_id} is missing an invoice or product ID.")
                    mode, store_id = sales_assignment(row["transaction_id"])
                    assigned = {**row, "sales_mode": mode, "store_id": store_id}
                    writers["assigned_sales.csv"].writerow(assigned)
                    if mode == "Online":
                        writers["online_sales_stream.csv"].writerow({name: assigned[name] for name in ONLINE_COLUMNS})
                    counts[mode.lower()] += 1
                    counts["total"] += 1
                    store_counts[store_id] += 1
                    customers.setdefault(row["customer_id"], row["country"])
                    product_id = row["product_id"]
                    description = row["product_name"]
                    if product_id not in products or len(description) > len(products[product_id]):
                        products[product_id] = description
                # Sorting output IDs makes repeatability explicit. Equal-length
                # description ties keep the first cleaned occurrence.
                for customer_id in sorted(customers):
                    attributes = customer_attributes(customer_id, customers[customer_id])
                    writers["customer_master_data.csv"].writerow(dict(zip(CUSTOMER_COLUMNS, attributes)))
                for product_id in sorted(products):
                    attributes = product_attributes(product_id, products[product_id])
                    writers["product_master_data.csv"].writerow(dict(zip(PRODUCT_COLUMNS, attributes)))
            publish_generated_files(staging_dir, output_dir)
            counts.update(customers=len(customers), products=len(products), stores=store_counts)
            return counts


def load_master_data(config, data_dir):
    """Replace both disk-backed masters together in one database transaction."""
    from retail_dw.database import database_connection, database_transaction

    data_dir = Path(data_dir).resolve()
    counts = {}
    with generated_data_lock(data_dir), ExitStack() as stack:
        readers = {}
        for table, columns in (("customer_master_data", CUSTOMER_COLUMNS), ("product_master_data", PRODUCT_COLUMNS)):
            handle = stack.enter_context((data_dir / f"{table}.csv").open(encoding="utf-8", newline=""))
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(columns):
                raise ValueError(f"Unexpected columns in {table}.csv.")
            readers[table] = (reader, columns)
        with database_connection(config) as connection, database_transaction(connection):
            cursor = connection.cursor()
            try:
                for table, (reader, columns) in readers.items():
                    cursor.execute(f"DELETE FROM {table}")
                    placeholders = ", ".join(["%s"] * len(columns))
                    statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                    batch = []
                    counts[table] = 0
                    for row in reader:
                        if None in row or any(value is None for value in row.values()):
                            raise ValueError(f"Malformed row in {table}.csv.")
                        batch.append(tuple(row[name] for name in columns))
                        if len(batch) == 500:
                            cursor.executemany(statement, batch)
                            counts[table] += len(batch)
                            batch.clear()
                    if batch:
                        cursor.executemany(statement, batch)
                        counts[table] += len(batch)
            finally:
                cursor.close()
    return counts
