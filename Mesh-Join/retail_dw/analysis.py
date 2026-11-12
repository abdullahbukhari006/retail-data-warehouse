"""Execute Q1-Q20 and compare all result rows with a cleaned-source oracle."""
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
from pathlib import Path
import re
import sqlite3
from mysql.connector import Error as MySQLError
from contextlib import closing
from tempfile import TemporaryDirectory

from retail_dw.database import database_connection
from retail_dw.reconciliation import Audit, build_expected
from retail_dw.rules import STORE_LIST

SCRIPT = Path(__file__).resolve().parents[2] / 'Queries-DW' / 'analysis_queries.sql'
ZERO = Decimal(0)
SIX = Decimal('0.000001')


def read_queries(path=SCRIPT):
    pieces = re.split(r'^-- Q(\d+)\s*$', Path(path).read_text(encoding='utf-8'), flags=re.M)
    queries = {}
    for index in range(1, len(pieces), 2):
        number = int(pieces[index])
        if number in queries:
            raise ValueError(f'Duplicate Q{number} marker.')
        sql = re.sub(r'^\s*--.*$', '', pieces[index+1], flags=re.M)
        queries[number] = [statement.strip() for statement in sql.split(';') if statement.strip()]
    if set(queries) != set(range(1,21)):
        raise ValueError('SQL script must contain exactly Q1 through Q20.')
    if any(len(statements) != (2 if n == 20 else 1) for n,statements in queries.items()):
        raise ValueError('Q1-Q19 need one SELECT each; Q20 needs CREATE VIEW and SELECT.')
    return queries


def rounded(value):
    return value.quantize(SIX, rounding=ROUND_HALF_UP)


def change(current, previous):
    return None if previous is None or previous == 0 else rounded(100*(current-previous)/previous)


def source_answers(db, audit):
    """Independent Python reductions; no warehouse query or stored time part is reused."""
    sums = {n: defaultdict(Decimal) for n in (1,2,3,4,6,7,8,9,10,12,13,14,15,17,19,20)}
    halves = defaultdict(lambda: [0,ZERO,0,ZERO,0,ZERO])
    product_names, supplier_names = {}, {}
    categories = set()
    suppliers = set()
    year = 2011
    sql = '''SELECT s.payload,c.payload,p.payload FROM source s
             JOIN attrs c ON c.kind='customer' AND c.id=json_extract(s.payload,'$.fact.customer_id')
             JOIN attrs p ON p.kind='product' AND p.id=json_extract(s.payload,'$.fact.product_id')'''
    for payload, customer_payload, product_payload in db.execute(sql):
        f = json.loads(payload)['fact']
        c, p = json.loads(customer_payload), json.loads(product_payload)
        event = datetime.fromisoformat(f['event_time'])
        d, m, q = event.date(), event.month, (event.month+2)//3
        day = 'Weekend' if event.weekday() >= 5 else 'Weekday'
        season = 'Spring' if m in (3,4,5) else 'Summer' if m in (6,7,8) else 'Fall' if m in (9,10,11) else 'Winter'
        rev, qty = Decimal(f['revenue']), int(f['quantity'])
        pid, name, category = p['product_id'], p['product_name'], p['product_category']
        sid, store = p['supplier_id'], int(f['store_id'])
        product_names[pid] = name
        supplier_names[sid] = p['supplier_name']
        suppliers.add(sid)
        categories.add(category)
        sums[2][c['gender'],c['age_group'],c['city_category']] += rev
        sums[3][category,c['occupation']] += rev
        if date(2011,7,1) <= d <= date(2011,12,9):
            sums[6][event.year,m,c['city_category'],c['marital_status']] += rev
        sums[7][f['transaction_id'],c['stay_in_current_city_years'],c['gender']] += rev
        sums[8][category,c['city_category']] += rev
        sums[13][store,sid,name] += rev
        sums[14][pid,name,season] += rev
        sums[19][pid,d] += rev
        sums[20][store,event.year,q] += rev
        if event.year == year:
            sums[1][year,m,day,pid,name] += rev
            sums[4][year,q,c['gender'],c['age_group']] += rev
            sums[9][category,m] += rev
            sums[10][c['age_group'],day] += rev
            sums[12][store,q] += rev
            sums[15][store,sid,m] += rev
            sums[17][store,sid,pid] += rev
            offset = 0 if m <= 6 else 2
            halves[pid][offset] += qty
            halves[pid][offset+1] += rev
            halves[pid][4] += qty
            halves[pid][5] += rev
    answers = {n: [] for n in range(1,21)}
    groups = defaultdict(list)
    for (y,m,day,pid,name),rev in sums[1].items():
        groups[y,m,day].append((pid,name,rev))
    for (y,m,day),products in groups.items():
        for rank,(pid,name,rev) in enumerate(sorted(products,key=lambda r:(-r[2],r[0]))[:5],1):
            answers[1].append(dict(year=y,month=m,day_type=day,product_rank=rank,product_id=pid,product_name=name,revenue=rev))
    answers[11] = list(answers[1])
    for n,columns in ((2,('gender','age_group','city_category')), (3,('product_category','occupation')),
                       (4,('year','quarter','gender','age_group')), (6,('year','month','city_category','marital_status'))):
        for key,rev in sums[n].items():
            answers[n].append(dict(zip((*columns,'revenue'),(*key,rev))))
    occupations = defaultdict(list)
    for (category,occupation),rev in sums[3].items():
        occupations[category].append((occupation,rev))
    for category,rows in occupations.items():
        for rank,(occupation,rev) in enumerate(sorted(rows,key=lambda r:(-r[1],r[0]))[:5],1):
            answers[5].append(dict(product_category=category,occupation_rank=rank,occupation=occupation,revenue=rev))
    purchases = defaultdict(list)
    for (invoice,stay,gender),rev in sums[7].items():
        purchases[stay,gender].append(rev)
    for (stay,gender),values in purchases.items():
        rev=sum(values,ZERO)
        answers[7].append(dict(stay_in_current_city_years=stay,gender=gender,purchases=len(values),revenue=rev,average_purchase=rounded(rev/len(values))))
    for category in categories:
        values={city:sums[8].get((category,city),ZERO) for city in 'ABC'}
        ranks={rev:i+1 for i,rev in enumerate(sorted(set(values.values()),reverse=True))}
        for city,rev in values.items():
            answers[8].append(dict(product_category=category,city_category=city,revenue=rev,city_rank=ranks[rev]))
    for category in categories:
        previous=None
        for month in range(1,13):
            rev=sums[9].get((category,month),ZERO)
            answers[9].append(dict(year=year,product_category=category,month=month,revenue=rev,previous_revenue=previous,growth_pct=change(rev,previous)))
            previous=rev
    for (age,day),rev in sums[10].items():
        answers[10].append(dict(year=year,age_group=age,day_type=day,revenue=rev))
    for store in STORE_LIST:
        previous=None
        for quarter in range(1,5):
            rev=sums[12].get((store,quarter),ZERO)
            answers[12].append(dict(year=year,store_id=store,store_name=STORE_LIST[store][0],quarter=quarter,revenue=rev,previous_revenue=previous,growth_pct=change(rev,previous)))
            previous=rev
    for (store,sid,name),rev in sums[13].items():
        answers[13].append(dict(store_id=store,store_name=STORE_LIST[store][0],supplier_id=sid,supplier_name=supplier_names[sid],product_name=name,revenue=rev))
    for (pid,name,season),rev in sums[14].items():
        answers[14].append(dict(product_id=pid,product_name=name,season=season,revenue=rev))
    for store in STORE_LIST:
        for sid in suppliers:
            previous=None
            for month in range(1,13):
                rev=sums[15].get((store,sid,month),ZERO)
                answers[15].append(dict(year=year,store_id=store,store_name=STORE_LIST[store][0],supplier_id=sid,supplier_name=supplier_names[sid],month=month,revenue=rev,previous_revenue=previous,volatility_pct=change(rev,previous)))
                previous=rev
    # Count unordered pairs of distinct cart products on disk, across all loaded dates.
    # The DISTINCT cart set uses the cleaned source, independently of the fact table.
    db.execute('CREATE TABLE carts AS SELECT DISTINCT '
               "json_extract(payload,'$.fact.transaction_id') AS invoice, "
               "json_extract(payload,'$.fact.product_id') AS product FROM source")
    db.execute('CREATE UNIQUE INDEX cart_lookup ON carts(invoice,product)')
    pairs=db.execute('SELECT a.product,b.product,COUNT(*) AS n FROM carts a JOIN carts b '
                     'ON a.invoice=b.invoice AND a.product<b.product GROUP BY a.product,b.product '
                     'ORDER BY n DESC,a.product,b.product LIMIT 5')
    for a,b,count in pairs:
        answers[16].append(dict(product_id_1=a,product_name_1=product_names[a],product_id_2=b,product_name_2=product_names[b],shopping_carts=count))
    supplier_totals,store_totals=defaultdict(Decimal),defaultdict(Decimal)
    for (store,sid,pid),rev in sums[17].items():
        answers[17].append(dict(year=year,store_id=store,store_name=STORE_LIST[store][0],supplier_id=sid,supplier_name=supplier_names[sid],product_id=pid,product_name=product_names[pid],aggregation_level='Product',revenue=rev))
        supplier_totals[store,sid]+=rev
        store_totals[store]+=rev
    for (store,sid),rev in supplier_totals.items():
        answers[17].append(dict(year=year,store_id=store,store_name=STORE_LIST[store][0],supplier_id=sid,supplier_name=supplier_names[sid],product_id=None,product_name='ALL PRODUCTS',aggregation_level='Supplier subtotal',revenue=rev))
    for store,rev in store_totals.items():
        answers[17].append(dict(year=year,store_id=store,store_name=STORE_LIST[store][0],supplier_id=None,supplier_name='ALL SUPPLIERS',product_id=None,product_name='ALL PRODUCTS',aggregation_level='Store subtotal',revenue=rev))
    answers[17].append(dict(year=year,store_id=None,store_name='ALL STORES',supplier_id=None,supplier_name='ALL SUPPLIERS',product_id=None,product_name='ALL PRODUCTS',aggregation_level='Grand total',revenue=sum(store_totals.values(),ZERO)))
    for pid,values in halves.items():
        answers[18].append(dict(year=year,product_id=pid,product_name=product_names[pid],**dict(zip(('first_half_quantity','first_half_revenue','second_half_quantity','second_half_revenue','full_year_quantity','full_year_revenue'),values))))
    product_totals=defaultdict(Decimal)
    for (pid,d),rev in sums[19].items():
        product_totals[pid]+=rev
    days=(audit.last_event.date()-audit.first_event.date()).days+1
    for (pid,d),rev in sums[19].items():
        total=product_totals[pid]
        if rev*days > 2*total:
            answers[19].append(dict(product_id=pid,product_name=product_names[pid],full_date=d,daily_revenue=rev,average_daily_revenue=rounded(total/days),spike_factor=rounded(rev*days/total),calendar_days=days))
    for (store,y,q),rev in sums[20].items():
        answers[20].append(dict(store_id=store,store_name=STORE_LIST[store][0],year=y,quarter=q,total_sales=rev))
    return answers


def canonical(row):
    def value(v):
        if isinstance(v,(int,float,Decimal)):
            return format(rounded(Decimal(str(v))),'f')
        if isinstance(v,(date,datetime)):
            return v.isoformat()
        return v
    return tuple(sorted((key,value(v)) for key,v in row.items()))


def compare_result(number, actual, expected):
    observed, wanted=Counter(map(canonical,actual)),Counter(map(canonical,expected))
    missing,extra=wanted-observed,observed-wanted
    failures=[]
    if missing or extra:
        failures.append(f'{sum(missing.values())} missing/different rows, {sum(extra.values())} extra/different rows')
        for label,rows in (('expected',missing),('observed',extra)):
            if rows:
                failures.append(f'{label} example: {dict(next(iter(rows)))}')
    if number == 20:
        keys=[(r['store_name'],r['year'],r['quarter']) for r in actual]
        if keys != sorted(keys):
            failures.append('view SELECT order differs from store name/year/quarter')
    if number == 16:
        keys=[(-r['shopping_carts'],r['product_id_1'],r['product_id_2']) for r in actual]
        if keys != sorted(keys):
            failures.append('product pair tie ordering is incorrect')
    return failures


def verify_queries(config, data_dir, write=print):
    queries=read_queries()
    results=[]
    data_dir=Path(data_dir).resolve()
    with localcontext() as context, TemporaryDirectory(prefix='olap_verification_',dir=data_dir) as folder:
        context.prec=50
        with closing(sqlite3.connect(str(Path(folder)/'reference.db'))) as db:
            audit=Audit()
            write('Reconstructing expected OLAP answers from the cleaned source on disk...')
            build_expected(db,data_dir,audit)
            if not audit.passed:
                raise ValueError('Prepared source violates assignment rules; run reconcile for diagnostics.')
            expected=source_answers(db,audit)
            write('Executing Q1-Q20 and comparing every result row with the source-derived answers...')
            with database_connection(config) as connection:
                # Q20 DDL necessarily commits; finish the read snapshot before creating the view.
                connection.start_transaction(isolation_level='REPEATABLE READ',consistent_snapshot=True,readonly=True)
                try:
                    for number in range(1,21):
                        if number == 20:
                            connection.rollback()
                        cursor=connection.cursor(dictionary=True)
                        try:
                            for sql in queries[number]:
                                cursor.execute(sql)
                            actual=cursor.fetchall()
                        except MySQLError as error:
                            raise RuntimeError(f'Q{number} execution failed (MySQL {error.errno}): {error.msg}') from error
                        finally:
                            cursor.close()
                        failures=compare_result(number,actual,expected[number])
                        results.append(dict(query=number,rows=len(actual),failures=failures))
                        write(f"Q{number:02}: {'FAIL' if failures else 'PASS'} ({len(actual):,} rows)")
                        for failure in failures:
                            write('  '+failure)
                        if number == 19:
                            # Persist a compact measured sample for the report, without credentials.
                            sample=sorted(actual,key=lambda r:(-r['spike_factor'],r['product_id'],str(r['full_date'])))[:5]
                            for row in sample:
                                write(f"  Spike: {row['product_id']} {row['full_date']} daily={row['daily_revenue']} average={row['average_daily_revenue']} factor={row['spike_factor']}")
                finally:
                    connection.rollback()
    passed=all(not result['failures'] for result in results)
    write('OLAP verification: '+('PASS (20/20)' if passed else 'FAIL'))
    return results
