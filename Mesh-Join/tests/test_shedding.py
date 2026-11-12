"""Priority, durable shedding, replay ownership, and restart regressions."""

from contextlib import closing
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import sqlite3
from threading import current_thread
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch

from retail_dw.buffer import PriorityStreamBuffer
from retail_dw.execution import RunControl
from retail_dw.meshjoin import MeshJoinThread, SalesTransaction
from retail_dw.runtime import check_threads
from retail_dw.shedding import ShedStore
from test_threads import ThreadCursor, ThreadFixture


def transaction(source_id, mode="Periodic"):
    return SalesTransaction(source_id,"invoice","C1001","P0501",2,Decimal("1.25"),
                            datetime(2011,1,1,10),mode,"Live" if mode == "Online" else "Periodic")


class PriorityTests(unittest.TestCase):
    def test_online_then_fresh_periodic_then_replay_with_stable_ties(self):
        buffer = PriorityStreamBuffer(maxsize=1000)
        buffer.put_replay_nowait(transaction(2))
        buffer.put_nowait(transaction(3))
        buffer.put_nowait(transaction(4,"Online"))
        buffer.put_nowait(transaction(5,"Online"))
        buffer.put_nowait(transaction(6))
        self.assertEqual([buffer.get_nowait().source_line_id for _ in range(5)],[4,5,3,6,2])

    def test_shedding_requires_full_buffer_and_never_takes_online_or_replay(self):
        buffer = PriorityStreamBuffer(maxsize=1000)
        store = Mock()
        buffer.put_nowait(transaction(2))
        self.assertEqual(buffer.shed_periodic(store),0)
        store.persist.assert_not_called()
        for index in range(3,1002):
            buffer.put_nowait(transaction(index,"Online"))
        self.assertEqual(buffer.shed_periodic(store),1)
        self.assertEqual(store.persist.call_args.args[0],[transaction(2)])
        buffer.put_replay_nowait(transaction(1002))
        store.persist.reset_mock()
        self.assertEqual(buffer.shed_periodic(store),0)
        store.persist.assert_not_called()

    def test_failed_persistence_keeps_all_buffer_ownership(self):
        buffer = PriorityStreamBuffer(maxsize=1000)
        for index in range(2,1002):
            buffer.put_nowait(transaction(index))
        store = Mock()
        store.persist.side_effect = OSError("Disk write failed")
        with self.assertRaises(OSError):
            buffer.shed_periodic(store)
        self.assertEqual(buffer.qsize(),1000)
        self.assertEqual(buffer.unfinished_tasks,1000)
        self.assertEqual(buffer.get_nowait(),transaction(2))


class ShedStoreTests(ThreadFixture, unittest.TestCase):
    def test_persist_is_idempotent_and_conflicts_roll_back(self):
        control = RunControl(self.settings)
        with closing(self.connect()) as connection:
            store = ShedStore(connection,control)
            original = transaction(100)
            store.persist([original])
            store.persist([original])
            self.assertEqual(store.counts(),(1,1))
            with self.assertRaisesRegex(ValueError,"conflicts"):
                store.persist([replace(original,quantity=99),transaction(101)])
            self.assertEqual(store.counts(),(1,1))
            with self.assertRaisesRegex(ValueError,"Only periodic"):
                store.persist([transaction(102,"Online")])

    def test_replay_does_not_delete_until_acknowledgement(self):
        control = RunControl(self.settings)
        control.set_stats(current_periodic_hour=0)
        buffer = PriorityStreamBuffer(maxsize=1000)
        with closing(self.connect()) as connection:
            store = ShedStore(connection,control)
            original = transaction(100)
            store.persist([original])
            self.assertEqual(store.replay(buffer),1)
            self.assertEqual(buffer.get_nowait(),original)
            self.assertEqual(store.counts(),(1,1))
            self.assertEqual(store.replay(buffer),0)
            self.assertEqual(store.acknowledge([100]),1)
            self.assertEqual(store.counts(),(0,0))

    def test_failed_ack_retains_disk_and_inflight_identity(self):
        control = RunControl(self.settings)
        control.set_stats(current_periodic_hour=0)
        with closing(self.connect()) as connection:
            store = ShedStore(connection,control)
            store.persist([transaction(100)])
            store.replay(PriorityStreamBuffer(maxsize=1000))
            execute = ThreadCursor.execute

            def failed_delete(cursor, sql, parameters=()):
                if sql.startswith("DELETE FROM shed_table"):
                    raise OSError("Delete failed")
                return execute(cursor,sql,parameters)

            with patch.object(ThreadCursor,"execute",failed_delete):
                with self.assertRaises(OSError):
                    store.acknowledge([100])
            self.assertEqual(store.counts(),(1,1))
            self.assertEqual(store.inflight,{100})
            self.assertEqual(store.acknowledge([100]),1)

    def test_existing_shed_rows_are_not_submitted_twice_on_restart(self):
        control = RunControl(self.settings)
        with closing(self.connect()) as connection:
            store = ShedStore(connection,control)
            store.persist([replace(transaction(100),transaction_id="periodic")])
        with patch("retail_dw.database.mysql.connector.connect",side_effect=self.connect):
            result = check_threads(self.config,self.root,self.root/"online_sales_stream.csv",self.settings,20,2)
        self.assertEqual(result["periodic_submitted"],6)
        self.assertEqual(result["enriched_periodic"],6)
        self.assertEqual(result["shed_replayed"],1)
        self.assertEqual(result["shed_pending"],0)
        self.assertEqual(self.released_count(),0)

    def test_peak_saturation_sheds_and_non_peak_recovers(self):
        with closing(sqlite3.connect(self.database)) as connection:
            rows = [(index,"periodic","C1001","P0501",2,"1.25","2011-01-01 10:00:00","Periodic","Periodic",0,0)
                    for index in range(1000,2500)]
            connection.executemany("INSERT INTO periodic_sales_data VALUES (?,?,?,?,?,?,?,?,?,?,?)",rows)
            connection.commit()
        settings = replace(self.settings,arrival_mode="peak",peak_rate=1000000)
        shared = {}
        original_execute = ThreadCursor.execute
        blocked = 0

        def capture_join(config, buffer, sink, control, **options):
            shared["buffer"] = buffer
            return MeshJoinThread(config,buffer,sink,control,**options)

        def slow_first_page(cursor, statement, parameters=()):
            nonlocal blocked
            if (current_thread().name == "MeshJoinThread" and "FROM customer_master_data" in statement
                    and "LIMIT" in statement and blocked < 2):
                blocked += 1
                deadline = monotonic()+3
                while not shared["buffer"].full() and monotonic() < deadline:
                    sleep(0.001)
                self.assertTrue(shared["buffer"].full())
            return original_execute(cursor,statement,parameters)

        with patch("retail_dw.database.mysql.connector.connect",side_effect=self.connect), patch("retail_dw.runtime.MeshJoinThread",side_effect=capture_join), patch.object(ThreadCursor,"execute",slow_first_page):
            result = check_threads(self.config,self.root,self.root/"online_sales_stream.csv",settings,20,1)
        self.assertGreater(result["shed_written"],0)
        self.assertEqual(result["shed_written"],result["shed_replayed"])
        self.assertEqual(result["shed_completed"],result["shed_replayed"])
        self.assertEqual(result["periodic_submitted"],1503)
        self.assertEqual(result["enriched_periodic"],1503)
        self.assertEqual(result["shed_pending"],0)
        self.assertTrue(result["automatic_recovery"])
        self.assertTrue(all(connection.closed for _,connection in self.connections))
