"""Reconciliation catches line-level corruption even when aggregate totals match."""
import csv
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retail_dw.meshjoin import EnrichedTransaction, SalesTransaction
from retail_dw.periodic import prepare_periodic_data, PERIODIC_COLUMNS
from retail_dw.prepare_data import CLEANED_COLUMNS, CUSTOMER_COLUMNS, PRODUCT_COLUMNS, generate_sales_data
from retail_dw.reconciliation import reconcile_warehouse
from retail_dw.warehouse import WarehouseLoader, flush_periodic_group
from test_warehouse import WarehouseFixture, WarehouseConnection


class AuditConnection(WarehouseConnection):
    def start_transaction(self, isolation_level=None, consistent_snapshot=False, readonly=False):
        if readonly:
            assert consistent_snapshot and isolation_level == 'REPEATABLE READ'
            self.raw.execute('PRAGMA query_only=ON')
            self.raw.execute('BEGIN')
        else:
            super().start_transaction(isolation_level)


class ReconciliationTests(WarehouseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.data=self.root/'audit-data'
        self.data.mkdir()
        with (self.data/'cleaned_sales.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=CLEANED_COLUMNS)
            writer.writeheader()
            for index in range(601):
                writer.writerow(dict(source_line_id=index+2,transaction_id=str(536365+index//3),
                    customer_id=str(100+index%7),product_id='P'+str(index%11),
                    product_name='Description'+(' longer' if index%4 else ''),quantity=2,
                    unit_price='0.15',event_time=str(datetime(2011,index%12+1,index%27+1,index%24)),
                    country='UK' if index<7 else 'France'))
        generate_sales_data(self.data/'cleaned_sales.csv',self.data)
        prepare_periodic_data(self.data)
        def read(name):
            with (self.data/name).open(encoding='utf-8',newline='') as handle:
                return list(csv.DictReader(handle))
        customers={r['customer_id']:r for r in read('customer_master_data.csv')}
        products={r['product_id']:r for r in read('product_master_data.csv')}
        with closing(sqlite3.connect(self.database)) as connection:
            for kind,columns,rows in (('customer',CUSTOMER_COLUMNS,customers.values()),('product',PRODUCT_COLUMNS,products.values())):
                connection.execute('DELETE FROM '+kind+'_master_data')
                connection.executemany('INSERT INTO '+kind+'_master_data VALUES ('+','.join('?' for _ in columns)+')',
                                       [tuple(row[c] for c in columns) for row in rows])
            connection.execute('DELETE FROM periodic_sales_data')
            connection.executemany('INSERT INTO periodic_sales_data VALUES ('+','.join('?' for _ in PERIODIC_COLUMNS)+')',
                [tuple(row[c] for c in PERIODIC_COLUMNS) for row in read('periodic_sales_data.csv')])
            connection.commit()
        assigned=read('assigned_sales.csv')
        with closing(self.connect()) as connection:
            loader=WarehouseLoader(connection)
            for start in range(0,len(assigned),500):
                loader.load_chunk([EnrichedTransaction(SalesTransaction.from_row(row),customers[row['customer_id']],products[row['product_id']])
                                   for row in assigned[start:start+500]])
            for hour in range(48):
                ids=[int(r['source_line_id']) for r in read('periodic_sales_data.csv') if int(r['release_hour'])==hour]
                connection.start_transaction()
                flush_periodic_group(connection,hour,ids)
                connection.raw.execute('UPDATE periodic_sales_data SET released=1 WHERE release_hour=?',(hour,))
                connection.commit()

    def audit(self):
        def connect(**kwargs):
            connection=AuditConnection(str(self.database),self.events)
            self.connections.append(('audit',connection))
            return connection
        with patch('mysql.connector.connect',side_effect=connect):
            return reconcile_warehouse(self.config,self.data,progress=lambda message:None)

    def change(self, sql):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(sql)
            connection.commit()

    def test_complete_audit_passes_and_database_remains_read_only(self):
        report=self.audit()
        self.assertTrue(report.passed,report.failures)
        self.assertEqual(report.actual['Total']['rows'],601)
        self.assertEqual(report.actual['Total']['revenue'],Decimal('180.30'))
        self.assertTrue(all(c.closed for _,c in self.connections))
        self.assertFalse(list(self.data.glob('reconciliation_*')))
        statements=[sql for name,when,sql in self.events if sql.startswith('SELECT f.sales_key')]
        self.assertGreaterEqual(len(statements),3)
        self.assertTrue(all('LIMIT 500' in sql for sql in statements))

    def test_offsetting_revenue_errors_fail_despite_equal_totals(self):
        self.change("UPDATE fact_sales SET revenue=CASE source_line_id WHEN 2 THEN '0.4' ELSE '0.2' END WHERE source_line_id IN (2,3)")
        report=self.audit()
        self.assertFalse(report.passed)
        self.assertEqual(report.actual['Total']['revenue'],report.expected['Total']['revenue'])
        self.assertEqual(report.failures['fact fields'],2)

    def test_missing_and_unexpected_ids_fail(self):
        self.change('UPDATE fact_sales SET source_line_id=99999 WHERE source_line_id=2')
        report=self.audit()
        self.assertEqual(report.failures['missing fact source ID'],1)
        self.assertEqual(report.failures['unexpected fact source ID'],1)

    def test_duplicate_source_identity_is_reported_if_constraint_is_missing(self):
        # Simulate a damaged import that bypassed the production unique key.
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('ALTER TABLE fact_sales RENAME TO saved_facts')
            connection.execute('CREATE TABLE fact_sales AS SELECT * FROM saved_facts')
            connection.execute('INSERT INTO fact_sales SELECT * FROM saved_facts WHERE source_line_id=2')
            connection.execute('UPDATE fact_sales SET sales_key=99999 WHERE rowid=(SELECT MAX(rowid) FROM fact_sales)')
            connection.commit()
        report=self.audit()
        self.assertEqual(report.failures['duplicate fact source ID'],1)
        self.assertEqual(report.actual['Total']['rows'],602)

    def test_orphan_dimension_modes_calendar_and_master_corruption_fail(self):
        self.change("UPDATE fact_sales SET customer_key=99999,processing_mode='wrong' WHERE source_line_id=2")
        self.change("UPDATE dim_time SET season='Wrong' WHERE time_key=1")
        self.change("UPDATE product_master_data SET supplier_name='Wrong' WHERE product_id='P0'")
        report=self.audit()
        self.assertIn('fact fields',report.failures)
        self.assertIn('dim_time attributes',report.failures)
        self.assertIn('product master attributes',report.failures)

    def test_unreleased_wrong_group_and_pending_disk_work_fail(self):
        self.change('UPDATE periodic_sales_data SET released=0,release_hour=47 WHERE source_line_id=(SELECT MIN(source_line_id) FROM periodic_sales_data)')
        self.change('INSERT INTO shed_table SELECT source_line_id,transaction_id,customer_id,product_id,quantity,unit_price,event_time,sales_mode,processing_mode,CURRENT_TIMESTAMP FROM periodic_sales_data LIMIT 1')
        report=self.audit()
        self.assertIn('staging fields/hour/released flag',report.failures)
        self.assertIn('pending shed_table',report.failures)

    def test_source_and_export_corruption_are_not_accepted_as_truth(self):
        path=self.data/'customer_master_data.csv'
        path.write_text(path.read_text(encoding='utf-8').replace('Male','Wrong'),encoding='utf-8')
        report=self.audit()
        self.assertIn('customer master CSV attributes',report.failures)
        path=self.data/'assigned_sales.csv'
        path.write_text(path.read_text(encoding='utf-8').replace('0.15','0.16',1),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'differs from cleaned'):
            self.audit()
        self.assertFalse(list(self.data.glob('reconciliation_*')))
