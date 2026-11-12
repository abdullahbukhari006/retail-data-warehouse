"""Consolidation, row identity, transactional loading, and hourly publication."""

from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import csv
import sqlite3
from threading import get_ident
import unittest
from unittest.mock import patch

from mysql.connector import Error as MySQLError

from retail_dw.database import database_transaction
from retail_dw.meshjoin import EnrichedTransaction, SalesTransaction
from retail_dw.prepare_data import CUSTOMER_COLUMNS, PRODUCT_COLUMNS
from retail_dw.runtime import run_warehouse
from retail_dw.warehouse import (
    DIMENSIONS, FACT_COLUMNS, WarehouseLoader, consolidate, flush_periodic_group,
)
from test_threads import ThreadConnection, ThreadCursor, ThreadFixture


def create_warehouse_tables(connection):
    connection.execute("PRAGMA foreign_keys=ON")
    for table, surrogate, columns in DIMENSIONS:
        integer_columns = {"store_id", "hour", "day_of_month", "month", "quarter", "year", "day_of_week", "half_year"}
        definitions = ', '.join(f"{name} {'INTEGER' if name in integer_columns else 'TEXT'} NOT NULL" for name in columns)
        connection.execute(f"CREATE TABLE {table} ({surrogate} INTEGER PRIMARY KEY AUTOINCREMENT, {definitions}, UNIQUE ({columns[0]}))")
    definitions = ', '.join(f"{name} {'TEXT' if name in ('transaction_id','revenue','sales_mode','processing_mode') else 'INTEGER'} NOT NULL" for name in FACT_COLUMNS)
    foreign_keys = ', '.join(f"FOREIGN KEY ({surrogate}) REFERENCES {table} ({surrogate})" for table,surrogate,_ in DIMENSIONS)
    connection.execute(f"CREATE TABLE fact_sales (sales_key INTEGER PRIMARY KEY AUTOINCREMENT, {definitions}, UNIQUE (source_line_id), {foreign_keys})")
    connection.execute(f"CREATE TABLE periodic_join_results ({definitions}, release_hour INTEGER NOT NULL, PRIMARY KEY (source_line_id), {foreign_keys})")


class WarehouseCursor(ThreadCursor):
    def execute(self, statement, parameters=()):
        if "CREATE TABLE IF NOT EXISTS periodic_join_results" in statement:
            return self.cursor.execute("SELECT 1 WHERE 0")
        self.connection.begin_write(statement)
        parameters = tuple(str(value) if isinstance(value,(Decimal,date)) else value for value in parameters)
        return super().execute(statement,parameters)

    def executemany(self, statement, parameters):
        self.connection.begin_write(statement)
        rows = [tuple(str(value) if isinstance(value,(Decimal,date)) else value for value in row) for row in parameters]
        return super().executemany(statement,rows)

    def fetchall(self):
        rows = super().fetchall()
        for row in rows:
            if isinstance(row.get("event_time"),str):
                row["event_time"] = datetime.fromisoformat(row["event_time"])
            if isinstance(row.get("full_date"),str):
                row["full_date"] = date.fromisoformat(row["full_date"])
            if "revenue" in row:
                row["revenue"] = Decimal(str(row["revenue"])).quantize(Decimal("0.000001"))
        return rows


class WarehouseConnection(ThreadConnection):
    """Statement-current reads adapt MySQL locking/RC reads for SQLite."""

    def __init__(self, path, events):
        super().__init__(path,events)
        self.raw.execute("PRAGMA foreign_keys=ON")
        self.logical_transaction = False
        self.read_committed = False

    def cursor(self, dictionary=False):
        assert self.owner == get_ident()
        return WarehouseCursor(self)

    @property
    def in_transaction(self):
        return self.logical_transaction or self.raw.in_transaction

    def start_transaction(self, isolation_level=None):
        assert not self.in_transaction
        self.read_committed = True
        self.logical_transaction = True

    def begin_write(self, statement):
        if self.read_committed and not self.raw.in_transaction and statement.lstrip().split()[0] in ("INSERT","UPDATE","DELETE"):
            self.raw.execute("BEGIN")

    def commit(self):
        super().commit()
        self.logical_transaction = False
        self.read_committed = False

    def rollback(self):
        super().rollback()
        self.logical_transaction = False
        self.read_committed = False


class WarehouseFixture(ThreadFixture):
    def setUp(self):
        super().setUp()
        with closing(sqlite3.connect(self.database)) as connection:
            create_warehouse_tables(connection)
            connection.commit()
        for name in ("assigned_sales.csv","online_sales_stream.csv"):
            path = self.root/name
            with path.open(encoding="utf-8",newline="") as handle:
                reader = csv.DictReader(handle)
                fields = reader.fieldnames
                rows = list(reader)
            with path.open("w",encoding="utf-8",newline="") as handle:
                writer = csv.DictWriter(handle,fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    row["transaction_id"] = "536365"
                    writer.writerow(row)

    def connect(self, **kwargs):
        connection = WarehouseConnection(str(self.database),self.events)
        self.connections.append(("fixture",connection))
        return connection

    def enriched(self, source_id, mode="Online"):
        transaction = SalesTransaction(source_id,"536365" if mode == "Online" else "periodic",
                                       "C1001","P0501",2,Decimal("1.25"),
                                       datetime(2011,1,1,10),mode,"Live" if mode == "Online" else "Periodic")
        customer = dict(zip(CUSTOMER_COLUMNS,("C1001","Male","18–25","Engineer","A","1","Single")))
        product = dict(zip(PRODUCT_COLUMNS,("P0501","Example","Kitchen","SUP01","Northwind Supplies")))
        return EnrichedTransaction(transaction,customer,product)

    def rows(self, table):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class WarehouseTests(WarehouseFixture, unittest.TestCase):
    def test_decimal_revenue_and_calendar_boundaries(self):
        original = self.enriched(2)
        enriched = replace(original,transaction=replace(original.transaction,quantity=3,unit_price=Decimal("0.10")))
        transaction,revenue,dimensions = consolidate(enriched)
        self.assertEqual(revenue,Decimal("0.300000"))
        self.assertEqual(dimensions[3],(1,"Online","Web"))
        for timestamp,season,half,quarter,day_type in (
            (datetime(2010,12,1),"Winter",2,4,"Weekday"),
            (datetime(2011,1,1),"Winter",1,1,"Weekend"),
            (datetime(2011,3,1),"Spring",1,1,"Weekday"),
            (datetime(2011,6,1),"Summer",1,2,"Weekday"),
            (datetime(2011,7,1),"Summer",2,3,"Weekday"),
            (datetime(2011,9,1),"Fall",2,3,"Weekday"),
        ):
            _,_,values = consolidate(replace(original,transaction=replace(transaction,event_time=timestamp)))
            time = values[4]
            self.assertEqual((time[9],time[10],time[5],time[8]),(season,half,quarter,day_type))

    def test_repeated_invoice_product_lines_keep_separate_source_facts(self):
        with closing(self.connect()) as connection:
            loader = WarehouseLoader(connection)
            rows = [self.enriched(2),self.enriched(3)]
            self.assertEqual(loader.load_chunk(rows)["online_inserted"],2)
            second = loader.load_chunk(rows)
            self.assertEqual(second["online_inserted"],0)
            self.assertEqual(second["already_loaded"],2)
        self.assertEqual(self.rows("fact_sales"),2)
        for table,_,_ in DIMENSIONS:
            self.assertEqual(self.rows(table),1)

    def test_periodic_batch_publishes_atomically_with_release_flags(self):
        rows = [self.enriched(index,"Periodic") for index in (100,101,102)]
        with closing(self.connect()) as connection:
            loader = WarehouseLoader(connection)
            self.assertEqual(loader.load_chunk(rows)["periodic_prepared"],3)
            self.assertEqual(loader.load_chunk(rows)["already_prepared"],3)
            self.assertEqual(self.rows("fact_sales"),0)
            self.assertEqual(self.rows("periodic_join_results"),3)
            with database_transaction(connection,isolation_level="READ COMMITTED"):
                self.assertEqual(flush_periodic_group(connection,0,[100,101,102]),3)
                connection.raw.execute("UPDATE periodic_sales_data SET released=1 WHERE release_hour=0")
        self.assertEqual(self.rows("fact_sales"),3)
        self.assertEqual(self.rows("periodic_join_results"),0)
        self.assertEqual(self.released_count(),3)

    def test_failed_periodic_commit_preserves_prepared_rows(self):
        with closing(self.connect()) as connection:
            WarehouseLoader(connection).load_chunk([self.enriched(100,"Periodic")])
            with self.assertRaises(OSError):
                with database_transaction(connection,isolation_level="READ COMMITTED"):
                    flush_periodic_group(connection,0,[100])
                    connection.raw.execute("UPDATE periodic_sales_data SET released=1 WHERE source_line_id=100")
                    raise OSError("Interrupted hourly publication")
        self.assertEqual(self.rows("fact_sales"),0)
        self.assertEqual(self.rows("periodic_join_results"),1)
        self.assertEqual(self.released_count(),0)

    def test_conflicting_fact_retry_rolls_back_new_dimensions(self):
        original = self.enriched(2)
        with closing(self.connect()) as connection:
            loader = WarehouseLoader(connection)
            loader.load_chunk([original])
            changed = replace(original,transaction=replace(original.transaction,quantity=99,event_time=datetime(2011,2,1)))
            with self.assertRaisesRegex(ValueError,"conflicts"):
                loader.load_chunk([changed])
        self.assertEqual(self.rows("fact_sales"),1)
        self.assertEqual(self.rows("dim_time"),1)

    def test_retryable_write_failure_retries_whole_transaction(self):
        execute = WarehouseCursor.executemany
        failed = False

        def interrupted(cursor, statement, values):
            nonlocal failed
            if "INSERT INTO fact_sales" in statement and not failed:
                failed = True
                raise MySQLError(errno=1213,msg="Simulated deadlock")
            return execute(cursor,statement,values)

        with closing(self.connect()) as connection, patch.object(WarehouseCursor,"executemany",interrupted):
            self.assertEqual(WarehouseLoader(connection).load_chunk([self.enriched(2)])["online_inserted"],1)
        self.assertEqual(self.rows("fact_sales"),1)

    def test_three_thread_load_and_repeat_do_not_duplicate_facts(self):
        with patch("retail_dw.database.mysql.connector.connect",side_effect=self.connect):
            first = run_warehouse(self.config,self.root,self.root/"online_sales_stream.csv",self.settings,20,2)
            second = run_warehouse(self.config,self.root,self.root/"online_sales_stream.csv",self.settings,20,2)
        self.assertEqual(first["warehouse"]["facts"],26)
        self.assertEqual(first["warehouse"]["pending_periodic"],0)
        self.assertEqual(first["periodic_inserted"],6)
        self.assertEqual(second["warehouse"]["facts"],26)
        self.assertEqual(second["online_inserted"],0)
        self.assertEqual(second["periodic_inserted"],0)
        self.assertEqual(self.released_count(),6)
        self.assertTrue(all(connection.closed for _,connection in self.connections))
