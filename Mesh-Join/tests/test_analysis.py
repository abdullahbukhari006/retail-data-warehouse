"""SQL answers compared with independent source reductions and boundary cases."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from retail_dw.analysis import read_queries, source_answers, compare_result, verify_queries, change
from retail_dw.reconciliation import Audit, build_expected
from test_reconciliation import ReconciliationFixture, AuditConnection
from test_warehouse import WarehouseCursor


class AnalysisCursor(WarehouseCursor):
    def execute(self, statement, parameters=()):
        # SQLite exercises CTEs/windows, but requires these MySQL dialect adapters.
        if 'GROUPING(st.store_id, sp.supplier_id, p.product_id)' in statement:
            start=statement.index('    SELECT st.store_id, sp.supplier_id, p.product_id,')
            end=statement.index('\n)\nSELECT 2011 AS year',start)
            inner=statement[start:end]
            tail=inner[inner.index('    FROM fact_sales f'):inner.index('    GROUP BY st.store_id')]
            parts=[]
            for expressions,mask,grouping in (
                ('st.store_id,sp.supplier_id,p.product_id',0,'st.store_id,sp.supplier_id,p.product_id'),
                ('st.store_id,sp.supplier_id,NULL',1,'st.store_id,sp.supplier_id'),
                ('st.store_id,NULL,NULL',3,'st.store_id'),('NULL,NULL,NULL',7,None)):
                parts.append('SELECT '+expressions+f', {mask} AS grouping_mask, SUM(f.revenue) AS revenue\n'+tail+
                             (' GROUP BY '+grouping if grouping else ''))
            statement=statement[:start]+' UNION ALL '.join(parts)+statement[end:]
        if statement.lstrip().startswith('CREATE OR REPLACE VIEW'):
            self.cursor.execute('DROP VIEW IF EXISTS STORE_QUARTERLY_SALES')
            statement=statement.replace('CREATE OR REPLACE VIEW','CREATE VIEW',1)
        return super().execute(statement,parameters)


class AnalysisConnection(AuditConnection):
    def __init__(self,path,events):
        super().__init__(path,events)
        self.raw.create_function('FIELD',-1,lambda value,*items: items.index(value)+1 if value in items else 0)
        self.raw.create_function('DATEDIFF',2,lambda last,first:(date.fromisoformat(last)-date.fromisoformat(first)).days)
        class ExactSum:
            def __init__(inner):
                inner.total=Decimal(0)
                inner.seen=False
                inner.integer=True
            def step(inner,value):
                if value is not None:
                    inner.seen=True
                    inner.integer &= isinstance(value,int)
                    inner.total+=Decimal(str(value))
            def finalize(inner):
                if not inner.seen:
                    return None
                return int(inner.total) if inner.integer else float(inner.total)
        self.raw.create_aggregate('SUM',1,ExactSum)
    def cursor(self,dictionary=False):
        return AnalysisCursor(self)
    def rollback(self):
        super().rollback()
        self.raw.execute('PRAGMA query_only=OFF')


class AnalysisTests(ReconciliationFixture, unittest.TestCase):
    # Exercise all answers on a realistic multi-page source and warehouse fixture.
    def test_all_sql_answers_match_source_oracle(self):
        lines=[]
        with patch('mysql.connector.connect',side_effect=lambda **kwargs:AnalysisConnection(str(self.database),self.events)):
            results=verify_queries(self.config,self.data,write=lines.append)
        self.assertEqual(len(results),20)
        self.assertTrue(all(not r['failures'] for r in results),lines)
        self.assertIn('OLAP verification: PASS (20/20)',lines)

    def test_wrong_result_and_view_order_are_rejected(self):
        expected=[dict(store_name='A',year=2011,quarter=1,total_sales=Decimal('1')),
                  dict(store_name='B',year=2011,quarter=1,total_sales=Decimal('2'))]
        self.assertTrue(compare_result(20,list(reversed(expected)),expected))
        self.assertTrue(compare_result(2,[dict(revenue=Decimal('3'))],[dict(revenue=Decimal('2'))]))
        self.assertIsNone(change(Decimal(10),None))
        self.assertIsNone(change(Decimal(10),Decimal(0)))
        self.assertEqual(change(Decimal(0),Decimal(10)),Decimal('-100.000000'))



    def test_hand_calculated_grains_ties_zero_months_and_pairs(self):
        import csv
        import sqlite3
        from contextlib import closing
        from retail_dw.prepare_data import CLEANED_COLUMNS, generate_sales_data
        data=self.root/'hand-calculated'
        data.mkdir()
        with (data/'cleaned_sales.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=CLEANED_COLUMNS)
            writer.writeheader()
            for i,product in enumerate(['T0','T1','T2','T3','T4','T5','T0','T0']):
                writer.writerow(dict(source_line_id=i+2,transaction_id='536365' if i<7 else '536366',
                    customer_id='100',product_id=product,product_name=product,quantity=1 if i<7 else 3,
                    unit_price='1',event_time='2011-01-03 10:00:00' if i<7 else '2011-03-07 10:00:00',country='UK'))
        generate_sales_data(data/'cleaned_sales.csv',data)
        with closing(sqlite3.connect(self.root/'hand-reference.db')) as db:
            audit=Audit()
            build_expected(db,data,audit)
            answers=source_answers(db,audit)
        january=[r for r in answers[1] if r['month']==1]
        self.assertEqual([r['product_id'] for r in january],['T0','T1','T2','T3','T4'])
        self.assertEqual(answers[7][0]['purchases'],2)
        self.assertEqual(answers[7][0]['average_purchase'],Decimal('5.000000'))
        from retail_dw.rules import product_attributes
        category=product_attributes('T0','T0')[2]
        monthly=sorted((r for r in answers[9] if r['product_category']==category),key=lambda r:r['month'])
        self.assertEqual(monthly[1]['growth_pct'],Decimal('-100.000000'))
        self.assertIsNone(monthly[2]['growth_pct'])
        self.assertEqual([r['shopping_carts'] for r in answers[16]],[1]*5)
        self.assertEqual([(r['product_id_1'],r['product_id_2']) for r in answers[16]],
                         [('T0','T1'),('T0','T2'),('T0','T3'),('T0','T4'),('T0','T5')])
        self.assertEqual({r['calendar_days'] for r in answers[19]},{64})
        self.assertEqual(sum(r['daily_revenue'] for r in answers[19]),Decimal(10))
        grand=[r for r in answers[17] if r['aggregation_level']=='Grand total']
        self.assertEqual(grand[0]['revenue'],Decimal(10))
        self.assertTrue(all(r['first_half_revenue']+r['second_half_revenue']==r['full_year_revenue'] for r in answers[18]))

    def test_sql_failure_names_the_question_and_cleans_up(self):
        from mysql.connector import Error as MySQLError
        execute=AnalysisCursor.execute
        def fail(cursor,statement,parameters=()):
            if 'monthly_product_sales' in statement:
                raise MySQLError(errno=1064,msg='Injected syntax failure')
            return execute(cursor,statement,parameters)
        with patch('mysql.connector.connect',side_effect=lambda **kwargs:AnalysisConnection(str(self.database),self.events)), patch.object(AnalysisCursor,'execute',fail):
            with self.assertRaisesRegex(RuntimeError,'Q1 execution failed'):
                verify_queries(self.config,self.data,write=lambda line:None)
        self.assertFalse(list(self.data.glob('olap_verification_*')))

    def test_q13_preserves_trailing_spaces_under_pad_space_collation(self):
        from contextlib import closing
        with closing(AnalysisConnection(str(self.database),self.events)) as connection:
            connection.raw.create_collation('PAD_SPACE',lambda a,b:(a.rstrip(' ')>b.rstrip(' '))-(a.rstrip(' ')<b.rstrip(' ')))
            connection.raw.execute("UPDATE dim_product SET product_name=CASE product_id WHEN 'P0' THEN 'CANDLE' ELSE 'CANDLE ' END WHERE product_id IN ('P0','P1')")
            connection.raw.execute('UPDATE fact_sales SET store_key=(SELECT store_key FROM dim_store WHERE store_id=1),supplier_key=(SELECT MIN(supplier_key) FROM dim_supplier)')
            connection.raw.commit()
            # Apply the production PAD SPACE comparison behavior to the test expression.
            sql=read_queries()[13][0].replace('p.product_name','(p.product_name COLLATE PAD_SPACE)')
            cursor=connection.cursor(dictionary=True)
            try:
                cursor.execute(sql)
                actual={row['product_name']:Decimal(str(row['revenue'])) for row in cursor.fetchall()}
            finally:
                cursor.close()
            expected={}
            for name,rev in connection.raw.execute("SELECT p.product_name,f.revenue FROM fact_sales f JOIN dim_product p ON p.product_key=f.product_key WHERE p.product_id IN ('P0','P1')"):
                expected[name]=expected.get(name,Decimal(0))+Decimal(str(rev))
            self.assertEqual(actual['CANDLE'],expected['CANDLE'])
            self.assertEqual(actual['CANDLE '],expected['CANDLE '])
            cursor=connection.cursor(dictionary=True)
            try:
                cursor.execute(sql.replace('HEX((p.product_name COLLATE PAD_SPACE))','(p.product_name COLLATE PAD_SPACE)'))
                merged=[r for r in cursor.fetchall() if r['product_name'].rstrip(' ')=='CANDLE']
            finally:
                cursor.close()
            self.assertEqual(len(merged),1)
            self.assertEqual(Decimal(str(merged[0]['revenue'])),expected['CANDLE']+expected['CANDLE '])
