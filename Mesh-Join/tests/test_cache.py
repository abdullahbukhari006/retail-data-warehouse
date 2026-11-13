"""Bonus cache atomicity, retries, scheduling, and direct-load equivalence."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mysql.connector import Error as MySQLError
from retail_dw.commands import OperatorConsole
from retail_dw.runtime import run_warehouse
from retail_dw.settings import RuntimeSettings
from retail_dw.warehouse import FACT_COLUMNS, WarehouseLoader, flush_periodic_group
from test_warehouse import WarehouseFixture, WarehouseCursor


class CacheTests(WarehouseFixture,unittest.TestCase):
    def test_missing_optional_table_is_empty_but_other_database_errors_propagate(self):
        with closing(self.connect()) as connection:
            with patch.object(WarehouseCursor,'execute',side_effect=MySQLError(errno=1146,msg='Missing optional table')):
                self.assertEqual(WarehouseLoader(connection).cache_count(),0)
            with patch.object(WarehouseCursor,'execute',side_effect=MySQLError(errno=1142,msg='Denied')):
                with self.assertRaises(MySQLError):
                    WarehouseLoader(connection).cache_count()
            self.assertFalse(connection.in_transaction)

    def fact_rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute('SELECT '+','.join(FACT_COLUMNS)+' FROM fact_sales ORDER BY source_line_id').fetchall()

    def test_cache_accepts_online_only_and_periodic_still_publishes(self):
        with closing(self.connect()) as connection:
            loader=WarehouseLoader(connection,'cache')
            result=loader.load_chunk([self.enriched(2),self.enriched(100,'Periodic')])
            self.assertEqual((result['online_cached'],result['online_inserted'],result['periodic_prepared']),(1,0,1))
            self.assertEqual(loader.cache_count(),1)
            self.assertEqual(self.rows('fact_sales'),0)
            connection.start_transaction()
            flush_periodic_group(connection,0,[100])
            connection.raw.execute('UPDATE periodic_sales_data SET released=1 WHERE source_line_id=100')
            connection.commit()
            self.assertEqual(self.rows('fact_sales'),1)
            self.assertEqual(loader.cache_count(),1)
            result=loader.flush_online_cache()
            self.assertEqual((result['online_inserted'],result['cache_transferred']),(1,1))
        self.assertEqual(self.rows('online_sales_cache'),0)
        self.assertEqual(self.rows('fact_sales'),2)

    def test_duplicate_cache_and_committed_fact_retries_are_idempotent(self):
        with closing(self.connect()) as connection:
            loader=WarehouseLoader(connection,'cache')
            self.assertEqual(loader.load_chunk([self.enriched(2)])['online_cached'],1)
            self.assertEqual(loader.load_chunk([self.enriched(2)])['already_cached'],1)
            self.assertEqual(loader.flush_online_cache()['online_inserted'],1)
            self.assertEqual(loader.load_chunk([self.enriched(2)])['online_cached'],1)
            result=loader.flush_online_cache()
            self.assertEqual((result['online_inserted'],result['cache_existing']),(0,1))
            self.assertEqual(loader.flush_online_cache()['cache_transferred'],0)
        self.assertEqual(self.rows('fact_sales'),1)

    def test_failed_delete_rolls_back_fact_insert_and_preserves_cache_for_restart(self):
        with closing(self.connect()) as connection:
            WarehouseLoader(connection,'cache').load_chunk([self.enriched(2),self.enriched(3)])
        execute=WarehouseCursor.execute
        def interrupted(cursor,statement,parameters=()):
            if statement.startswith('DELETE FROM online_sales_cache'):
                raise OSError('Injected transfer interruption')
            return execute(cursor,statement,parameters)
        with closing(self.connect()) as connection,patch.object(WarehouseCursor,'execute',interrupted):
            with self.assertRaises(OSError):
                WarehouseLoader(connection,'cache').flush_online_cache()
        self.assertEqual(self.rows('fact_sales'),0)
        self.assertEqual(self.rows('online_sales_cache'),2)
        with closing(self.connect()) as connection:
            self.assertEqual(WarehouseLoader(connection).flush_online_cache()['online_inserted'],2)
        self.assertEqual(self.rows('online_sales_cache'),0)

    def test_conflicting_cached_identity_rolls_back_new_dimensions_in_both_modes(self):
        with closing(self.connect()) as connection:
            WarehouseLoader(connection,'cache').load_chunk([self.enriched(2)])
            original=self.enriched(2)
            changed=replace(original,transaction=replace(original.transaction,quantity=99,event_time=datetime(2011,2,1)))
            for mode in ('direct','cache'):
                with self.assertRaisesRegex(ValueError,'Cached fact conflicts'):
                    WarehouseLoader(connection,mode).load_chunk([changed])
        self.assertEqual(self.rows('dim_time'),1)
        self.assertEqual(self.rows('fact_sales'),0)
        self.assertEqual(self.rows('online_sales_cache'),1)

    def test_conflicting_committed_fact_never_deletes_cache(self):
        with closing(self.connect()) as connection:
            WarehouseLoader(connection).load_chunk([self.enriched(2)])
            loader=WarehouseLoader(connection,'cache')
            loader.load_chunk([self.enriched(2)])
            connection.raw.execute('UPDATE fact_sales SET quantity=99')
            connection.raw.commit()
            with self.assertRaisesRegex(ValueError,'Conflicting stored transaction'):
                loader.flush_online_cache()
        self.assertEqual(self.rows('online_sales_cache'),1)

    def test_cache_transfer_is_bounded_to_500_and_retries_deadlock(self):
        with closing(self.connect()) as connection:
            loader=WarehouseLoader(connection,'cache')
            loader.load_chunk([self.enriched(i) for i in range(2,502)])
            loader.load_chunk([self.enriched(502)])
            execute=WarehouseCursor.executemany
            failed=False
            def deadlock(cursor,statement,parameters):
                nonlocal failed
                if 'INSERT INTO fact_sales' in statement and not failed:
                    failed=True
                    raise MySQLError(errno=1213,msg='Injected deadlock')
                return execute(cursor,statement,parameters)
            with patch.object(WarehouseCursor,'executemany',deadlock):
                self.assertEqual(loader.flush_online_cache()['cache_transferred'],500)
            self.assertEqual(loader.cache_count(),1)
            self.assertEqual(loader.flush_online_cache()['cache_transferred'],1)
        self.assertEqual(self.rows('fact_sales'),501)

    def test_non_live_online_result_is_rejected_without_changing_flags(self):
        original=self.enriched(2)
        altered=replace(original,transaction=replace(original.transaction,processing_mode='Other'))
        with closing(self.connect()) as connection:
            with self.assertRaisesRegex(ValueError,'only Online/Live'):
                WarehouseLoader(connection,'cache').load_chunk([altered])
        self.assertEqual(self.rows('online_sales_cache'),0)
        self.assertEqual(self.rows('dim_customer'),0)

    def test_batch_direct_cache_equivalence_and_existing_fact_replay(self):
        with patch('mysql.connector.connect',side_effect=self.connect):
            direct=run_warehouse(self.config,self.root,self.root/'online_sales_stream.csv',self.settings,20,2)
            baseline=self.fact_rows()
            with closing(sqlite3.connect(self.database)) as connection:
                connection.execute('DELETE FROM fact_sales')
                connection.execute('UPDATE periodic_sales_data SET released=0')
                connection.commit()
            settings=RuntimeSettings(arrival_mode='peak',peak_rate=1000,seconds_per_hour=1)
            cached=run_warehouse(self.config,self.root,self.root/'online_sales_stream.csv',settings,20,2,loading_mode='cache')
            self.assertTrue(cached['automatic_recovery'])
            self.assertEqual(cached['online_cached'],20)
            self.assertEqual(cached['cache_transferred'],20)
            self.assertEqual(cached['online_inserted'],20)
            self.assertGreater(cached['cache_high_water'],0)
            self.assertEqual(cached['warehouse']['pending_online_cache'],0)
            self.assertEqual(self.fact_rows(),baseline)
            again=run_warehouse(self.config,self.root,self.root/'online_sales_stream.csv',settings,20,2,loading_mode='cache')
            self.assertEqual(again['online_inserted'],0)
            self.assertEqual(again['cache_existing'],20)
            self.assertEqual(self.fact_rows(),baseline)
        self.assertTrue(all(connection.closed for _,connection in self.connections))

    def test_interactive_peak_keeps_cache_while_periodic_publishes_then_non_peak_drains(self):
        observed=[]
        class ScriptConsole(OperatorConsole):
            def start(inner):
                pass
            def poll(inner):
                stats=inner.control.snapshot()
                if not observed and stats['cache_pending']==20 and stats['groups_released']==2:
                    with closing(sqlite3.connect(self.database)) as connection:
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM fact_sales WHERE sales_mode='Online'").fetchone()[0],0)
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM fact_sales WHERE sales_mode='Periodic'").fetchone()[0],6)
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM online_sales_cache WHERE sales_mode!='Online' OR processing_mode!='Live'").fetchone()[0],0)
                    observed.append('peak cached, periodic published')
                    inner.execute('status')
                    inner.execute('non-peak')
                elif stats.get('workers_complete'):
                    inner.execute('quit')
        settings=RuntimeSettings(arrival_mode='peak',peak_rate=1000,seconds_per_hour=1)
        with patch('mysql.connector.connect',side_effect=self.connect):
            result=run_warehouse(self.config,self.root,self.root/'online_sales_stream.csv',settings,20,2,
                interactive=True,loading_mode='cache',console_factory=lambda c,b:ScriptConsole(c,b,write=lambda text:None))
        self.assertEqual(len(observed),1)
        self.assertEqual(result['cache_transferred'],20)
        self.assertEqual(result['warehouse']['facts'],26)
        self.assertFalse(result.get('automatic_recovery',False))
