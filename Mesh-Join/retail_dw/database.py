"""Open a fresh connection per caller, with explicit transaction boundaries."""

from contextlib import contextmanager
from dataclasses import dataclass, field

import mysql.connector


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    def __post_init__(self):
        if not 1 <= self.port <= 65535:
            raise ValueError("Database port must be between 1 and 65535.")
        if not all(value.strip() for value in (self.host, self.database, self.user)):
            raise ValueError("Host, database, and username are required.")


@contextmanager
def database_connection(config):
    """Call inside the owning thread. Connections are never shared or pooled."""
    connection = mysql.connector.connect(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.user,
        password=config.password,
        charset="utf8mb4",
        autocommit=False,
        connection_timeout=10,
    )
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def database_transaction(connection, isolation_level=None):
    """Commit the whole operation, or roll it back when the operation fails."""
    if isolation_level is None:
        connection.start_transaction()
    else:
        connection.start_transaction(isolation_level=isolation_level)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def check_database(config):
    """Read server and selected schema information without modifying data."""
    with database_connection(config) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT VERSION(), DATABASE()")
            version, database = cursor.fetchone()
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
            )
            tables = {row[0] for row in cursor.fetchall()}
        finally:
            cursor.close()
    required = {
        "customer_master_data", "product_master_data", "dim_customer",
        "dim_product", "dim_supplier", "dim_store", "dim_time",
        "fact_sales", "periodic_sales_data", "shed_table",
    }
    missing = required - tables
    if missing:
        raise ValueError("Missing project tables: " + ", ".join(sorted(missing)))
    return version, database

