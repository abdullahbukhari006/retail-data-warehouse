"""Regression checks for page coverage, staggered chunks, and safe handoff."""

from contextlib import ExitStack, closing
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from queue import Full
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retail_dw.meshjoin import MASTER_COLUMNS, MasterPageReader, MeshJoin, SalesTransaction
from retail_dw.settings import RuntimeSettings


class SQLiteCursor:
    """Adapt only the cursor calls used by MasterPageReader for disk fixtures."""

    def __init__(self, connection):
        self.cursor = connection.raw.cursor()
        self.queries = connection.queries

    def execute(self, statement, parameters=()):
        self.queries.append((statement, parameters))
        self.cursor.execute(statement.replace("%s", "?"), parameters)

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]

    def close(self):
        self.cursor.close()


class SQLiteConnection:
    def __init__(self, path):
        self.raw = sqlite3.connect(path)
        self.raw.row_factory = sqlite3.Row
        self.queries = []

    def cursor(self, dictionary=False):
        if not dictionary:
            raise AssertionError("Master scans must request dictionary rows.")
        return SQLiteCursor(self)

    def close(self):
        self.raw.close()


class MeshJoinTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(TemporaryDirectory())
        self.connection = self.stack.enter_context(closing(SQLiteConnection(str(Path(folder) / "masters.db"))))
        for table, columns in MASTER_COLUMNS.items():
            definitions = [f"{name} TEXT NOT NULL" for name in columns]
            self.connection.raw.execute(f"CREATE TABLE {table} ({', '.join(definitions)}, PRIMARY KEY ({columns[0]}))")
        self.connection.raw.executemany(
            "INSERT INTO customer_master_data VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(f"C{index:04}", "Male", "18–25", "Engineer", "A", "1", "Single")
             for index in range(1, 1002)],
        )
        self.connection.raw.executemany(
            "INSERT INTO product_master_data VALUES (?, ?, ?, ?, ?)",
            [(f"P{index:04}", f"Product {index}", "Kitchen", "SUP01", "Northwind Supplies")
             for index in range(1, 502)],
        )
        self.connection.raw.commit()
        self.customer = self.stack.enter_context(closing(MasterPageReader(self.connection, "customer_master_data")))
        self.product = self.stack.enter_context(closing(MasterPageReader(self.connection, "product_master_data")))

    def transaction(self, source_id, customer="C1001", product="P0501"):
        return SalesTransaction(source_id, "invoice-1", customer, product, 2, Decimal("1.25"),
                                datetime(2011, 1, 1, 10), "Periodic", "Periodic")

    def test_cyclic_pages_include_short_final_page(self):
        pages = [self.customer.read_next_page() for _ in range(4)]
        self.assertEqual([len(page) for page in pages], [500, 500, 1, 500])
        self.assertEqual(pages[0], pages[3])
        self.assertEqual(pages[2][0]["customer_id"], "C1001")
        selects = [query for query, _ in self.connection.queries if "LIMIT" in query]
        self.assertEqual(len(selects), 4)
        self.assertTrue(all(query.endswith("LIMIT %s") for query in selects))

    def test_full_cycles_and_multimap_preserve_shared_references(self):
        outputs = []
        join = MeshJoin(self.customer, self.product, outputs.extend)
        transactions = [self.transaction(index) for index in range(2, 502)]
        for transaction in transactions:
            join.stream_buffer.put_nowait(transaction)
        join.step()
        self.assertEqual(join.active_count, 500)
        self.assertEqual(outputs, [])
        state = join.H[("customer", "C1001")][2]
        self.assertIs(state, join.H[("product", "P0501")][2])
        self.assertIs(state, join.queue[-1].members[0])
        self.assertIsNone(state.customer)
        join.step()
        self.assertEqual(outputs, [])
        self.assertIsNotNone(state.product)
        join.step()
        self.assertEqual([row.transaction for row in outputs], transactions)
        self.assertEqual(join.active_count, 0)
        self.assertEqual(join.H, {})
        self.assertFalse(join.queue)
        self.assertEqual(join.stream_buffer.unfinished_tasks, 0)
        self.assertTrue(all(row.product["supplier_id"] == "SUP01" for row in outputs))

    def test_staggered_chunks_join_from_every_starting_page(self):
        outputs = []
        join = MeshJoin(self.customer, self.product, outputs.extend)
        expected = [replace(self.transaction(index), customer_id=f"C{1 + index % 1001:04}",
                            product_id=f"P{1 + index % 501:04}") for index in range(2, 1503)]
        pending = iter(expected)
        exhausted = False
        while not exhausted or join.has_work:
            while not exhausted and not join.stream_buffer.full():
                transaction = next(pending, None)
                if transaction is None:
                    exhausted = True
                else:
                    join.stream_buffer.put_nowait(transaction)
            join.step()
            self.assertLessEqual(join.active_count, 1000)
            self.assertTrue(all(len(page) <= 500 for page in join.disk_buffers.values()))
        self.assertEqual([row.transaction for row in outputs], expected)
        self.assertEqual(join.iterations, 6)
        self.assertEqual(join.admitted, join.emitted)
        self.assertEqual(join.stream_buffer.unfinished_tasks, 0)
        self.assertEqual(self.customer.pages_read, 6)
        self.assertEqual(self.product.pages_read, 6)

    def test_buffer_is_bounded_and_idle_step_does_not_read_pages(self):
        join = MeshJoin(self.customer, self.product, lambda output: None)
        self.assertEqual(join.step(), 0)
        self.assertEqual(self.customer.pages_read, 0)
        for index in range(2, 1002):
            join.stream_buffer.put_nowait(self.transaction(index))
        with self.assertRaises(Full):
            join.stream_buffer.put_nowait(self.transaction(1002))

    def test_missing_match_stops_without_discarding_chunk(self):
        outputs = []
        join = MeshJoin(self.customer, self.product, outputs.extend)
        join.stream_buffer.put_nowait(self.transaction(2, product="MISSING"))
        join.step()
        join.step()
        with self.assertRaisesRegex(ValueError, "unmatched"):
            join.step()
        self.assertEqual(outputs, [])
        self.assertEqual(join.active_count, 1)
        self.assertEqual(len(join.queue), 1)
        self.assertTrue(join.failed)

    def test_failed_output_is_retried_before_further_scanning(self):
        delivered = []
        attempts = 0

        def sink(output):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("Downstream transaction failed")
            delivered.extend(output)

        join = MeshJoin(self.customer, self.product, sink)
        join.stream_buffer.put_nowait(self.transaction(2))
        join.step()
        join.step()
        with self.assertRaises(OSError):
            join.step()
        self.assertEqual(join.emitted, 0)
        self.assertEqual(join.active_count, 1)
        self.assertEqual(join.step(), 1)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(self.customer.pages_read, 3)
        self.assertEqual(self.product.pages_read, 3)

    def test_partial_read_failure_cannot_advance_coverage(self):
        join = MeshJoin(self.customer, self.product, lambda output: None)
        join.stream_buffer.put_nowait(self.transaction(2))
        join.step()
        with patch.object(self.product, "read_next_page", side_effect=OSError("Disk failure")):
            with self.assertRaises(OSError):
                join.step()
        self.assertEqual(join.queue[-1].iterations, 1)
        self.assertTrue(join.failed)
        with self.assertRaisesRegex(ValueError, "stopped"):
            join.step()

    def test_empty_master_is_rejected(self):
        self.connection.raw.execute("DELETE FROM product_master_data")
        with self.assertRaisesRegex(ValueError, "empty"):
            MasterPageReader(self.connection, "product_master_data")

    def test_row_conversion_preserves_supplied_mode_and_decimal_price(self):
        row = dict(source_line_id="2", transaction_id="invoice", customer_id="C0001",
                   product_id="P0001", quantity="2", unit_price="0.10",
                   event_time="2011-01-01 10:00:00", sales_mode="Online", processing_mode="Live")
        transaction = SalesTransaction.from_row(row)
        self.assertEqual(transaction.unit_price, Decimal("0.10"))
        self.assertEqual(transaction.processing_mode, "Live")
        with self.assertRaises(ValueError):
            SalesTransaction.from_row({**row, "quantity": "1.5"})

    def test_required_buffer_and_chunk_settings_are_enforced(self):
        for name in ("stream_buffer_limit", "stream_chunk_size", "master_page_size", "periodic_groups"):
            with self.subTest(setting=name), self.assertRaises(ValueError):
                RuntimeSettings(**{name: 0})


if __name__ == "__main__":
    unittest.main()
