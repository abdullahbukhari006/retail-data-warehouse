"""Concurrency, release timing, acknowledgement, and cancellation checks."""

import csv
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from queue import Queue
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Event, current_thread, get_ident
from time import monotonic, sleep
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retail_dw.database import DatabaseConfig
from retail_dw.execution import RunControl
from retail_dw.meshjoin import MASTER_COLUMNS, MeshJoinThread
from retail_dw.online import OnlineThread
from retail_dw.periodic import PERIODIC_COLUMNS, PeriodicThread
from retail_dw.prepare_data import GENERATED_COLUMNS, ONLINE_COLUMNS
from retail_dw.runtime import check_threads
from retail_dw.settings import RuntimeSettings
from test_meshjoin import SQLiteConnection, SQLiteCursor


class ThreadCursor(SQLiteCursor):
    def __init__(self, connection):
        super().__init__(connection)
        self.connection = connection

    def execute(self, statement, parameters=()):
        assert self.connection.owner == get_ident()
        self.connection.events.append((current_thread().name, monotonic(), statement))
        super().execute(statement.replace(" FOR UPDATE", ""), parameters)

    def executemany(self, statement, parameters):
        assert self.connection.owner == get_ident()
        rows = [tuple(str(value) if isinstance(value, (Decimal, datetime)) else value for value in row)
                for row in parameters]
        self.cursor.executemany(statement.replace("%s", "?"), rows)

    @property
    def rowcount(self):
        return self.cursor.rowcount


class ThreadConnection(SQLiteConnection):
    def __init__(self, path, events):
        super().__init__(path)
        self.owner = get_ident()
        self.events = events
        self.closed = False

    def cursor(self, dictionary=False):
        assert dictionary and self.owner == get_ident()
        return ThreadCursor(self)

    @property
    def in_transaction(self):
        return self.raw.in_transaction

    def start_transaction(self):
        assert self.owner == get_ident()
        self.raw.execute("BEGIN")

    def commit(self):
        assert self.owner == get_ident()
        self.raw.commit()

    def rollback(self):
        assert self.owner == get_ident()
        self.raw.rollback()

    def close(self):
        assert self.owner == get_ident()
        self.raw.close()
        self.closed = True


class ThreadFixture:
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.database = self.root / "threads.db"
        self.events = []
        self.connections = []
        self.config = DatabaseConfig("fixture", 3306, "fixture", "fixture", "test-only")
        self.settings = RuntimeSettings(non_peak_rate=1000, peak_rate=2000, seconds_per_hour=1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            for table, columns in MASTER_COLUMNS.items():
                definitions = ', '.join(f'{name} TEXT NOT NULL' for name in columns)
                connection.execute(f"CREATE TABLE {table} ({definitions}, PRIMARY KEY ({columns[0]}))")
            connection.executemany(
                "INSERT INTO customer_master_data VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(f"C{index:04}", "Male", "18–25", "Engineer", "A", "1", "Single") for index in range(1,1002)],
            )
            connection.executemany(
                "INSERT INTO product_master_data VALUES (?, ?, ?, ?, ?)",
                [(f"P{index:04}", "Example", "Kitchen", "SUP01", "Northwind Supplies") for index in range(1,502)],
            )
            integer_columns = {"source_line_id", "quantity", "release_hour", "released"}
            definitions = ', '.join(f"{name} {'INTEGER' if name in integer_columns else 'TEXT'} NOT NULL" for name in PERIODIC_COLUMNS)
            connection.execute(f"CREATE TABLE periodic_sales_data ({definitions}, PRIMARY KEY (source_line_id))")
            values = [(index+100,"periodic","C1001","P0501",2,"1.25","2011-01-01 10:00:00","Periodic","Periodic",index//3,0) for index in range(6)]
            connection.executemany("INSERT INTO periodic_sales_data VALUES (?,?,?,?,?,?,?,?,?,?,?)",values)
            shed_columns = PERIODIC_COLUMNS[:9]
            definitions = ', '.join(f"{name} {'INTEGER' if name in integer_columns else 'TEXT'} NOT NULL" for name in shed_columns)
            connection.execute(f"CREATE TABLE shed_table ({definitions}, shed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (source_line_id))")
            connection.commit()
        self.write_online()

    def write_online(self, product="P0501"):
        with (self.root/"assigned_sales.csv").open("w",encoding="utf-8",newline="") as assigned_file:
            with (self.root/"online_sales_stream.csv").open("w",encoding="utf-8",newline="") as online_file:
                assigned = csv.DictWriter(assigned_file,fieldnames=GENERATED_COLUMNS["assigned_sales.csv"])
                online = csv.DictWriter(online_file,fieldnames=ONLINE_COLUMNS)
                assigned.writeheader()
                online.writeheader()
                for index in range(20):
                    row = dict(source_line_id=index+2,transaction_id="online",customer_id="C1001",product_id=product,
                               product_name="Example",quantity=2,unit_price="1.25",event_time="2011-01-01 10:00:00",
                               country="UK",sales_mode="Online",store_id=1)
                    assigned.writerow(row)
                    online.writerow({name:row[name] for name in ONLINE_COLUMNS})

    def connect(self, **kwargs):
        connection = ThreadConnection(str(self.database), self.events)
        self.connections.append((current_thread().name, connection))
        return connection

    def released_count(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute("SELECT SUM(released) FROM periodic_sales_data").fetchone()[0]


class ThreadTests(ThreadFixture, unittest.TestCase):

    def test_three_workers_reconcile_and_observe_hourly_interval(self):
        begin = ThreadConnection.start_transaction
        first = True

        def delayed_begin(connection):
            nonlocal first
            if first:
                first = False
                sleep(0.15)
            begin(connection)

        with patch("retail_dw.database.mysql.connector.connect",side_effect=self.connect), patch.object(ThreadConnection,"start_transaction",delayed_begin):
            result = check_threads(self.config,self.root,self.root/"online_sales_stream.csv",self.settings,20,2)
        self.assertEqual(result["online_submitted"],20)
        self.assertEqual(result["enriched_online"],20)
        self.assertEqual(result["periodic_submitted"],6)
        self.assertEqual(result["enriched_periodic"],6)
        self.assertEqual(result["groups_released"],2)
        self.assertEqual(result["stream_buffer"],0)
        self.assertEqual({name for name,_ in self.connections},{"PeriodicThread","MeshJoinThread"})
        self.assertTrue(all(connection.closed for _,connection in self.connections))
        selections = [when for name,when,sql in self.events if name == "PeriodicThread" and "FOR UPDATE" in sql]
        self.assertEqual(len(selections),2)
        self.assertGreaterEqual(selections[1]-selections[0],0.99)
        self.assertEqual(self.released_count(),0)

    def test_online_arrivals_continue_during_master_read(self):
        control = RunControl(self.settings)
        buffer = Queue(maxsize=1000)
        periodic = PeriodicThread(self.config,buffer,control,hours=1)
        online = OnlineThread(self.root/"online_sales_stream.csv",self.root/"assigned_sales.csv",buffer,control,20)
        output = []

        def acknowledge(rows):
            output.extend(rows)
            periodic.acknowledge(row.transaction.source_line_id for row in rows)

        join = MeshJoinThread(self.config,buffer,acknowledge,control)
        blocked, resume = Event(), Event()
        execute = ThreadCursor.execute

        def delayed(cursor, statement, parameters=()):
            if current_thread().name == "MeshJoinThread" and "LIMIT" in statement and not blocked.is_set():
                blocked.set()
                if not resume.wait(5):
                    raise TimeoutError("Test did not release the master read")
            return execute(cursor,statement,parameters)

        workers = [join,online,periodic]
        with patch("retail_dw.database.mysql.connector.connect",side_effect=self.connect), patch.object(ThreadCursor,"execute",delayed):
            try:
                join.start()
                self.assertTrue(control.ready.wait(2))
                online.start()
                periodic.start()
                self.assertTrue(blocked.wait(2))
                deadline = monotonic()+2
                while control.snapshot()["online_submitted"] < 3 and monotonic() < deadline:
                    sleep(0.005)
                self.assertGreaterEqual(control.snapshot()["online_submitted"],3)
                self.assertGreater(buffer.qsize(),0)
                resume.set()
                online.join(3)
                periodic.join(3)
            finally:
                resume.set()
                control.stop.set()
                control.producers_done.set()
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(3)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertTrue(control.errors.empty())
            self.assertEqual(len(output),23)

    def test_full_buffer_put_can_be_cancelled(self):
        control = RunControl(self.settings)
        buffer = Queue(maxsize=1000)
        for _ in range(1000):
            buffer.put_nowait(object())
        online = OnlineThread(self.root/"online_sales_stream.csv",self.root/"assigned_sales.csv",buffer,control)
        online.start()
        control.stop.set()
        online.join(1)
        self.assertFalse(online.is_alive())
        self.assertTrue(control.errors.empty())

    def test_failed_join_stops_producers_and_rolls_back_release(self):
        self.write_online(product="MISSING")
        with patch("retail_dw.database.mysql.connector.connect",side_effect=self.connect):
            with self.assertRaisesRegex(RuntimeError,"MeshJoinThread failed"):
                check_threads(self.config,self.root,self.root/"online_sales_stream.csv",self.settings,20,2)
        self.assertTrue(all(connection.closed for _,connection in self.connections))
        self.assertEqual(self.released_count(),0)

    def test_speed_change_wakes_online_rate_wait(self):
        settings = RuntimeSettings(non_peak_rate=1,peak_rate=100)
        control = RunControl(settings)
        buffer = Queue(maxsize=1000)
        online = OnlineThread(self.root/"online_sales_stream.csv",self.root/"assigned_sales.csv",buffer,control,3)
        try:
            online.start()
            deadline = monotonic()+1
            while control.snapshot()["online_submitted"] < 1 and monotonic() < deadline:
                sleep(0.005)
            self.assertEqual(control.snapshot()["online_submitted"],1)
            control.set_speed("peak")
            online.join(0.5)
            self.assertFalse(online.is_alive())
            self.assertEqual(control.snapshot()["online_submitted"],3)
        finally:
            control.stop.set()
            online.join(1)

    def test_coordinator_cancellation_drains_accepted_work(self):
        def interruptible_join(config, buffer, sink, control, **options):
            worker = MeshJoinThread(config, buffer, sink, control, **options)
            wait = worker.finished.wait

            def interrupted_wait(timeout=None):
                if control.snapshot()["online_submitted"] >= 2:
                    raise KeyboardInterrupt
                return wait(timeout)

            worker.finished.wait = interrupted_wait
            return worker

        with patch("retail_dw.database.mysql.connector.connect",side_effect=self.connect), patch("retail_dw.runtime.MeshJoinThread",side_effect=interruptible_join):
            result = check_threads(self.config,self.root,self.root/"online_sales_stream.csv",self.settings,20,2)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["online_submitted"],result["enriched_online"])
        self.assertEqual(result["periodic_submitted"],result["enriched_periodic"])
        self.assertEqual(result["stream_buffer"],0)
        self.assertTrue(all(connection.closed for _,connection in self.connections))
        self.assertEqual(self.released_count(),0)

    def test_release_commits_only_after_all_acknowledgements(self):
        control = RunControl(self.settings)
        buffer = Queue(maxsize=1000)
        periodic = PeriodicThread(self.config,buffer,control,hours=1,commit_releases=True)
        with patch("retail_dw.database.mysql.connector.connect",side_effect=self.connect):
            try:
                periodic.start()
                rows = [buffer.get(timeout=2) for _ in range(3)]
                self.assertEqual(self.released_count(),0)
                periodic.acknowledge([row.source_line_id for row in rows])
                periodic.join(2)
                self.assertFalse(periodic.is_alive())
                self.assertTrue(control.errors.empty())
                self.assertEqual(self.released_count(),3)
            finally:
                control.stop.set()
                periodic.join(2)


if __name__ == "__main__":
    unittest.main()
