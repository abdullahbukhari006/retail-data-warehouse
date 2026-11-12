"""Read the original workbook and prepare cleaned transaction lines."""

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tempfile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from zipfile import BadZipFile


SOURCE_COLUMNS = (
    "InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
    "UnitPrice", "CustomerID", "Country",
)
CLEANED_COLUMNS = (
    "source_line_id", "transaction_id", "customer_id", "product_id",
    "product_name", "quantity", "unit_price", "event_time", "country",
)


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
