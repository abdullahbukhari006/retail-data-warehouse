"""Consolidate joined lines and atomically load dimensions and sales facts."""

from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from time import sleep

from mysql.connector import Error as MySQLError

from retail_dw.database import database_connection, database_transaction
from retail_dw.meshjoin import SalesTransaction
from retail_dw.prepare_data import CUSTOMER_COLUMNS, PRODUCT_COLUMNS
from retail_dw.rules import STORE_LIST, sales_assignment


TIME_COLUMNS = (
    "event_time", "full_date", "hour", "day_of_month", "month", "quarter",
    "year", "day_of_week", "day_type", "season", "half_year",
)
FACT_COLUMNS = (
    "source_line_id", "transaction_id", "customer_key", "product_key",
    "supplier_key", "store_key", "time_key", "quantity", "revenue",
    "sales_mode", "processing_mode",
)
DIMENSIONS = (
    ("dim_customer", "customer_key", CUSTOMER_COLUMNS),
    ("dim_product", "product_key", PRODUCT_COLUMNS[:3]),
    ("dim_supplier", "supplier_key", ("supplier_id", "supplier_name")),
    ("dim_store", "store_key", ("store_id", "store_name", "city")),
    ("dim_time", "time_key", TIME_COLUMNS),
)
SEASONS = ("Winter", "Winter", "Spring", "Spring", "Spring", "Summer",
           "Summer", "Summer", "Fall", "Fall", "Fall", "Winter")


def consolidate(enriched):
    transaction = enriched.transaction
    if (enriched.customer.get("customer_id") != transaction.customer_id
            or enriched.product.get("product_id") != transaction.product_id):
        raise ValueError("Consolidation requires both correct master matches.")
    mode, store_id = sales_assignment(transaction.transaction_id)
    if mode != transaction.sales_mode:
        raise ValueError("Transaction sales mode conflicts with the prescribed invoice rule.")
    with localcontext() as context:
        context.prec = 50
        revenue = transaction.unit_price * transaction.quantity
        try:
            stored_revenue = revenue.quantize(Decimal("0.000001"))
        except InvalidOperation as error:
            raise ValueError("Revenue exceeds the warehouse decimal precision.") from error
        if revenue != stored_revenue or not 0 < revenue < Decimal("1000000000000000000"):
            raise ValueError("Revenue cannot be represented exactly as DECIMAL(24, 6).")
    event = transaction.event_time
    time = (
        event, event.date(), event.hour, event.day, event.month,
        (event.month - 1) // 3 + 1, event.year, event.isoweekday(),
        "Weekday" if event.weekday() < 5 else "Weekend",
        SEASONS[event.month - 1], 1 if event.month <= 6 else 2,
    )
    try:
        dimensions = (
            tuple(enriched.customer[name] for name in CUSTOMER_COLUMNS),
            tuple(enriched.product[name] for name in PRODUCT_COLUMNS[:3]),
            (enriched.product["supplier_id"], enriched.product["supplier_name"]),
            (store_id, *STORE_LIST[store_id]), time,
        )
    except KeyError as error:
        raise ValueError(f"Enrichment is missing a dimension attribute: {error}") from error
    return transaction, stored_revenue, dimensions


def select_rows(cursor, table, columns, ids, locking=False):
    if not ids:
        return {}
    ordered_ids = tuple(sorted(ids))
    statement = (
        f"SELECT {', '.join(columns)} FROM {table} WHERE {columns[0]} "
        f"IN ({', '.join(['%s'] * len(ordered_ids))}) ORDER BY {columns[0]}"
    )
    if locking:
        statement += " FOR UPDATE"
    cursor.execute(statement, ordered_ids)
    return {row[columns[0]]: row for row in cursor.fetchall()}


def insert_checked(cursor, table, columns, rows):
    """Insert missing identities and reject conflicting retries."""
    expected = {row[0]: row for row in rows}
    existing = select_rows(cursor, table, columns, expected, locking=True)
    for identity, row in existing.items():
        if tuple(row[name] for name in columns) != expected[identity]:
            raise ValueError(f"Conflicting stored transaction in {table}, source line {identity}.")
    fresh = [expected[identity] for identity in sorted(expected) if identity not in existing]
    if fresh:
        cursor.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})", fresh,
        )
    return len(fresh)


def dimension_keys(cursor, table, surrogate, columns, rows):
    expected = {}
    for row in rows:
        previous = expected.setdefault(row[0], row)
        if previous != row:
            raise ValueError(f"Conflicting dimension attributes within {table} batch.")
    # Dimension attributes are immutable. Existing rows require no updates.
    existing = select_rows(cursor, table, (*columns, surrogate), expected)
    for identity, row in existing.items():
        if tuple(row[name] for name in columns) != expected[identity]:
            raise ValueError(f"Existing {table} attributes conflict for {identity}.")
    fresh = [expected[identity] for identity in sorted(expected) if identity not in existing]
    if fresh:
        cursor.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})", fresh,
        )
        existing = select_rows(cursor, table, (*columns, surrogate), expected)
    return {identity: row[surrogate] for identity, row in existing.items()}


class WarehouseLoader:
    """One instance and connection owned by MeshJoinThread."""

    def __init__(self, connection, loading_mode="direct"):
        if loading_mode not in ("direct", "cache"):
            raise ValueError("Loading mode must be direct or cache.")
        self.connection = connection
        self.loading_mode = loading_mode

    def ensure_schema(self):
        cursor = self.connection.cursor()
        try:
            for filename in ("add_periodic_join_results.sql", "add_online_sales_cache.sql"):
                migration = Path(__file__).resolve().parents[2] / "Create-DW" / filename
                cursor.execute(migration.read_text(encoding="utf-8"))
        finally:
            cursor.close()

    def load_chunk(self, output):
        if not output:
            return dict(online_inserted=0, periodic_prepared=0, already_loaded=0, already_prepared=0)
        consolidated = [consolidate(row) for row in output]
        if len({row[0].source_line_id for row in consolidated}) != len(consolidated):
            raise ValueError("Warehouse chunk contains duplicate source-line IDs.")
        for attempt in range(3):
            try:
                return self._load_chunk(consolidated)
            except MySQLError as error:
                if error.errno not in (1062, 1205, 1213) or attempt == 2:
                    raise
                sleep(0.05 * (attempt + 1))

    def _load_chunk(self, consolidated):
        with database_transaction(self.connection, isolation_level="READ COMMITTED"):
            cursor = self.connection.cursor(dictionary=True)
            try:
                keys = []
                for index, (table, surrogate, columns) in enumerate(DIMENSIONS):
                    keys.append(dimension_keys(cursor, table, surrogate, columns,
                                               [row[2][index] for row in consolidated]))
                facts = []
                original = {}
                for transaction, revenue, dimensions in consolidated:
                    fact = (
                        transaction.source_line_id, transaction.transaction_id,
                        *(mapping[values[0]] for mapping, values in zip(keys, dimensions)),
                        transaction.quantity, revenue, transaction.sales_mode, transaction.processing_mode,
                    )
                    facts.append(fact)
                    original[transaction.source_line_id] = transaction
                existing = select_rows(cursor, "fact_sales", FACT_COLUMNS, original, locking=True)
                for fact in facts:
                    previous = existing.get(fact[0])
                    if previous is not None and tuple(previous[name] for name in FACT_COLUMNS) != fact:
                        raise ValueError(f"Existing fact conflicts with source line {fact[0]}.")
                all_online = [row for row in facts if row[-2] == "Online"]
                cached = select_rows(cursor, "online_sales_cache", FACT_COLUMNS,
                                     {row[0] for row in all_online}, locking=True)
                for fact in all_online:
                    previous = cached.get(fact[0])
                    if previous is not None and tuple(previous[name] for name in FACT_COLUMNS) != fact:
                        raise ValueError(f"Cached fact conflicts with source line {fact[0]}.")
                online = [row for row in all_online if row[0] not in existing]
                periodic = [row for row in facts if row[0] not in existing and row[-2] == "Periodic"]
                cache_inserted = 0
                if self.loading_mode == "cache":
                    if any(row[-1] != "Live" for row in all_online):
                        raise ValueError("The online cache accepts only Online/Live results.")
                    # Even a matching retry goes through the cache in this mode.
                    # Transfer verifies an already committed fact before removing its cache row.
                    cache_inserted = insert_checked(cursor, "online_sales_cache", FACT_COLUMNS, all_online)
                    inserted = 0
                else:
                    inserted = insert_checked(cursor, "fact_sales", FACT_COLUMNS, online)
                periodic_original = {row[0]: original[row[0]] for row in periodic}
                staging_columns = (
                    "source_line_id", "transaction_id", "customer_id", "product_id", "quantity",
                    "unit_price", "event_time", "sales_mode", "processing_mode", "release_hour", "released",
                )
                staged = select_rows(cursor, "periodic_sales_data", staging_columns, periodic_original)
                prepared = []
                for fact in periodic:
                    row = staged.get(fact[0])
                    if row is None or row["released"] or SalesTransaction.from_row(row) != periodic_original[fact[0]]:
                        raise ValueError(f"Periodic staging conflicts with source line {fact[0]}.")
                    prepared.append((*fact, row["release_hour"]))
                prepared_count = insert_checked(cursor, "periodic_join_results", (*FACT_COLUMNS, "release_hour"), prepared)
                return dict(online_inserted=inserted, periodic_prepared=prepared_count,
                            already_loaded=len(existing), already_prepared=len(prepared) - prepared_count,
                            online_cached=cache_inserted,
                            already_cached=len(all_online) - cache_inserted if self.loading_mode == "cache" else 0)
            finally:
                cursor.close()

    def cache_count(self):
        with database_transaction(self.connection, isolation_level="READ COMMITTED"):
            return online_cache_count(self.connection)

    def flush_online_cache(self):
        """Called by the join worker only during a non-peak scheduling window."""
        for attempt in range(3):
            try:
                return self._flush_online_cache()
            except MySQLError as error:
                if error.errno not in (1062, 1205, 1213) or attempt == 2:
                    raise
                sleep(0.05 * (attempt + 1))

    def _flush_online_cache(self):
        with database_transaction(self.connection, isolation_level="READ COMMITTED"):
            cursor = self.connection.cursor(dictionary=True)
            try:
                cursor.execute(f"SELECT {', '.join(FACT_COLUMNS)} FROM online_sales_cache "
                               "ORDER BY source_line_id LIMIT 500 FOR UPDATE")
                rows = cursor.fetchall()
                if any(row["sales_mode"] != "Online" or row["processing_mode"] != "Live" for row in rows):
                    raise ValueError("The online cache contains a non-Online/Live result.")
                facts = [tuple(row[name] for name in FACT_COLUMNS) for row in rows]
                inserted = insert_checked(cursor, "fact_sales", FACT_COLUMNS, facts)
                if rows:
                    ids = tuple(row["source_line_id"] for row in rows)
                    cursor.execute("DELETE FROM online_sales_cache WHERE source_line_id IN ("
                                   + ", ".join(["%s"] * len(ids)) + ")", ids)
                return dict(online_inserted=inserted, cache_transferred=len(rows),
                            cache_existing=len(rows) - inserted)
            finally:
                cursor.close()


def flush_periodic_group(connection, hour, source_ids):
    """Use PeriodicThread's existing transaction for facts, cleanup, and flags."""
    if not source_ids:
        return 0
    cursor = connection.cursor(dictionary=True)
    try:
        prepared = select_rows(cursor, "periodic_join_results", (*FACT_COLUMNS, "release_hour"), source_ids, locking=True)
        existing = select_rows(cursor, "fact_sales", FACT_COLUMNS, source_ids, locking=True)
        if set(prepared) | set(existing) != set(source_ids):
            raise ValueError("Hourly warehouse batch is missing completed join results.")
        if any(row["release_hour"] != hour or row["sales_mode"] != "Periodic" for row in prepared.values()):
            raise ValueError("Completed periodic results have incorrect release metadata.")
        facts = [tuple(row[name] for name in FACT_COLUMNS) for row in prepared.values()]
        inserted = insert_checked(cursor, "fact_sales", FACT_COLUMNS, facts)
        if prepared:
            cursor.execute(
                f"DELETE FROM periodic_join_results WHERE source_line_id IN ({', '.join(['%s'] * len(prepared))})",
                tuple(sorted(prepared)),
            )
        return inserted
    finally:
        cursor.close()


def online_cache_count(connection):
    """An absent optional cache is empty on installations preceding the bonus migration."""
    cursor = connection.cursor(dictionary=True)
    try:
        try:
            cursor.execute("SELECT COUNT(*) AS pending FROM online_sales_cache")
            return cursor.fetchall()[0]["pending"]
        except MySQLError as error:
            if error.errno != 1146:
                raise
            return 0
    finally:
        cursor.close()


def warehouse_summary(config):
    with database_connection(config) as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("SELECT COUNT(*) AS facts, COALESCE(SUM(quantity), 0) AS quantity, "
                           "COALESCE(SUM(revenue), 0) AS revenue FROM fact_sales")
            result = cursor.fetchall()[0]
            cursor.execute("SELECT COUNT(*) AS pending FROM periodic_join_results")
            result["pending_periodic"] = cursor.fetchall()[0]["pending"]
            result["pending_online_cache"] = online_cache_count(connection)
            return result
        finally:
            cursor.close()
