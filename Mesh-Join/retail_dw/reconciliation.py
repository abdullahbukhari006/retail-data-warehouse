"""Read-only, paged reconciliation against independently reconstructed source rules."""
import csv
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from retail_dw.database import database_connection
from retail_dw.prepare_data import CLEANED_COLUMNS, GENERATED_COLUMNS, CUSTOMER_COLUMNS, PRODUCT_COLUMNS, ONLINE_COLUMNS
from retail_dw.periodic import PERIODIC_COLUMNS
from retail_dw.rules import customer_attributes, product_attributes, sales_assignment, STORE_LIST
from retail_dw.warehouse import DIMENSIONS


def normalized(row):
    return {k: (v.isoformat(sep=' ') if isinstance(v, datetime) else
                format(v.normalize(), 'f') if isinstance(v, Decimal) else
                None if v is None else str(v)) for k, v in row.items()}


def csv_rows(path, fields):
    with path.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(fields):
            raise ValueError(f'Unexpected columns in {path.name}.')
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'Malformed row in {path.name}.')
            yield row


class Audit:
    def __init__(self):
        self.failures = {}
        self.examples = {}
        self.expected = {}
        self.actual = {}
        self.dimensions = {}
        self.first_event = None
        self.last_event = None
        self.group_sizes = []

    def fail(self, check, detail):
        self.failures[check] = self.failures.get(check, 0) + 1
        samples = self.examples.setdefault(check, [])
        if len(samples) < 5:
            samples.append(str(detail))

    @property
    def passed(self):
        return not self.failures

    def print_summary(self):
        print('Warehouse reconciliation: ' + ('PASS' if self.passed else 'FAIL'))
        for mode in ('Online', 'Periodic', 'Total'):
            e, a = self.expected[mode], self.actual[mode]
            print(f"{mode}: source/warehouse rows {e['rows']:,}/{a['rows']:,}; "
                  f"quantity {e['quantity']:,}/{a['quantity']:,}; "
                  f"revenue {e['revenue']:,.6f}/{a['revenue']:,.6f}")
        print('Dimension rows checked: ' + ', '.join(f'{k}={v:,}' for k,v in self.dimensions.items()))
        print(f'Event-time coverage: {self.first_event} through {self.last_event}')
        print(f'Periodic groups: 48; smallest/largest: {min(self.group_sizes):,}/{max(self.group_sizes):,}')
        print('Checks: exact source-line identity and fields, dimension/master attributes, invoice channel/store, '
              'decimal revenue, calendar fields, hourly staging order/flags, and empty durable backlogs.')
        for check, count in sorted(self.failures.items()):
            print(f'FAIL {check}: {count:,}; examples: ' + '; '.join(self.examples[check]))


def totals():
    return {mode: dict(rows=0, quantity=0, revenue=Decimal(0)) for mode in ('Online','Periodic','Total')}


def add_total(target, mode, quantity, revenue):
    for key in (mode, 'Total') if mode in ('Online','Periodic') else ('Total',):
        target[key]['rows'] += 1
        target[key]['quantity'] += quantity
        target[key]['revenue'] += revenue


def calendar(event):
    # Construct independently of the warehouse consolidation function.
    month = event.month
    season = ('Spring' if month in (3,4,5) else 'Summer' if month in (6,7,8)
              else 'Fall' if month in (9,10,11) else 'Winter')
    return normalized(dict(event_time=event, full_date=event.date(), hour=event.hour,
        day_of_month=event.day, month=month, quarter=(month+2)//3, year=event.year,
        day_of_week=event.weekday()+1, day_type='Weekend' if event.weekday() >= 5 else 'Weekday',
        season=season, half_year=1 if month < 7 else 2))


def build_expected(db, data_dir, audit):
    db.executescript('''CREATE TABLE source (id INTEGER PRIMARY KEY, payload TEXT, mode TEXT,
                       event TEXT, seen INTEGER DEFAULT 0, release_hour INTEGER);
        CREATE TABLE attrs (kind TEXT, id TEXT, payload TEXT, seen INTEGER DEFAULT 0,
                            PRIMARY KEY(kind,id));
        CREATE TABLE descriptions (id TEXT PRIMARY KEY, description TEXT);
        CREATE INDEX source_event_order ON source(mode,event,id);''')
    assigned = csv_rows(data_dir/'assigned_sales.csv', GENERATED_COLUMNS['assigned_sales.csv'])
    cleaned = csv_rows(data_dir/'cleaned_sales.csv', CLEANED_COLUMNS)
    online = csv_rows(data_dir/'online_sales_stream.csv', ONLINE_COLUMNS)
    previous = 1
    audit.expected = totals()
    try:
        for row in assigned:
            original = next(cleaned, None)
            if original != {name:row[name] for name in CLEANED_COLUMNS}:
                raise ValueError('Assigned input differs from cleaned input; regenerate matching data.')
            identity = int(row['source_line_id'])
            quantity, price = Decimal(row['quantity']), Decimal(row['unit_price'])
            if (identity <= previous or not quantity.is_finite() or not price.is_finite()
                    or quantity <= 0 or quantity != quantity.to_integral_value() or price <= 0
                    or not row['customer_id'].strip() or not row['product_id'].strip()
                    or not row['transaction_id'].strip() or row['transaction_id'].upper().startswith('C')):
                raise ValueError(f'Invalid cleaned source line {identity}.')
            previous = identity
            mode, store = sales_assignment(row['transaction_id'])
            if row['sales_mode'] != mode or row['store_id'] != str(store):
                audit.fail('source channel/store rule', identity)
            if mode == 'Online' and next(online, None) != {name:row[name] for name in ONLINE_COLUMNS}:
                audit.fail('online CSV identity/order', identity)
            event = datetime.fromisoformat(row['event_time'])
            if event.tzinfo is not None:
                raise ValueError('Source timestamps must be timezone-naive.')
            if audit.first_event is None or event < audit.first_event:
                audit.first_event = event
            if audit.last_event is None or event > audit.last_event:
                audit.last_event = event
            revenue = quantity * price
            if revenue != revenue.quantize(Decimal('0.000001')):
                raise ValueError(f'Unrepresentable source revenue at {identity}.')
            fact = normalized(dict(transaction_id=row['transaction_id'], customer_id=row['customer_id'],
                product_id=row['product_id'], supplier_id=product_attributes(row['product_id'],'')[3],
                store_id=store, event_time=event, quantity=int(quantity), revenue=revenue,
                sales_mode=mode, processing_mode='Live' if mode == 'Online' else 'Periodic'))
            staged = normalized(dict(source_line_id=identity,transaction_id=row['transaction_id'],
                customer_id=row['customer_id'],product_id=row['product_id'],quantity=int(quantity),
                unit_price=price,event_time=event,sales_mode=mode,processing_mode='Periodic'))
            db.execute('INSERT INTO source(id,payload,mode,event) VALUES (?,?,?,?)',
                       (identity,json.dumps(dict(fact=fact,staging=staged)),mode,str(event)))
            add_total(audit.expected,mode,int(quantity),revenue)
            cust = normalized(dict(zip(CUSTOMER_COLUMNS, customer_attributes(row['customer_id'],row['country']))))
            db.execute('INSERT OR IGNORE INTO attrs(kind,id,payload) VALUES (?,?,?)',
                       ('customer',row['customer_id'],json.dumps(cust)))
            db.execute('INSERT INTO descriptions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET '
                       'description=excluded.description WHERE length(excluded.description)>length(description)',
                       (row['product_id'],row['product_name']))
            db.execute('INSERT OR IGNORE INTO attrs(kind,id,payload) VALUES (?,?,?)',
                       ('time',str(event),json.dumps(calendar(event))))
        if next(cleaned,None) is not None or next(online,None) is not None:
            raise ValueError('Extra rows in cleaned or online CSV.')
    finally:
        assigned.close()
        cleaned.close()
        online.close()
    if not audit.expected['Total']['rows']:
        raise ValueError('Cannot certify an empty source dataset.')
    for identity,description in db.execute('SELECT id,description FROM descriptions'):
        product = normalized(dict(zip(PRODUCT_COLUMNS,product_attributes(identity,description))))
        db.execute('INSERT INTO attrs(kind,id,payload) VALUES (?,?,?)',('product',identity,json.dumps(product)))
    for identity, name in STORE_LIST.items():
        db.execute('INSERT INTO attrs(kind,id,payload) VALUES (?,?,?)',
                   ('store',str(identity),json.dumps(normalized(dict(store_id=identity,store_name=name[0],city=name[1])))))
    # Only suppliers referenced by the source products belong in this warehouse.
    for (payload,) in db.execute("SELECT payload FROM attrs WHERE kind='product'"):
        product=json.loads(payload)
        supplier={k:product[k] for k in ('supplier_id','supplier_name')}
        db.execute('INSERT OR IGNORE INTO attrs(kind,id,payload) VALUES (?,?,?)',
                   ('supplier',product['supplier_id'],json.dumps(supplier)))
    count=audit.expected['Periodic']['rows']
    base,remainder=divmod(count,48)
    audit.group_sizes = [base + (hour < remainder) for hour in range(48)]
    ordered=db.execute("SELECT id FROM source WHERE mode='Periodic' ORDER BY event,id")
    for hour in range(48):
        for _ in range(base+(hour<remainder)):
            db.execute('UPDATE source SET release_hour=? WHERE id=?',(hour,next(ordered)[0]))
    db.commit()


def pages(connection, table, key, columns):
    last = None
    while True:
        cursor = connection.cursor(dictionary=True)
        try:
            where = '' if last is None else f' WHERE {key} > %s'
            cursor.execute(f'SELECT {columns} FROM {table}{where} ORDER BY {key} LIMIT 500',
                           () if last is None else (last,))
            rows=cursor.fetchall()
        finally:
            cursor.close()
        if not rows:
            return
        last=rows[-1][key.split('.')[-1]]
        yield from rows


def compare_attrs(db, audit, kind, rows, columns, label):
    db.execute('UPDATE attrs SET seen=0 WHERE kind=?',(kind,))
    count=0
    for row in rows:
        count+=1
        values=normalized({name:row.get(name) for name in columns})
        identity=values[columns[0]]
        expected=db.execute('SELECT payload,seen FROM attrs WHERE kind=? AND id=?',(kind,identity)).fetchone()
        if expected is None:
            audit.fail(label+' unexpected identity',identity)
        else:
            if expected[1]:
                audit.fail(label+' duplicate identity',identity)
            wanted=json.loads(expected[0])
            if values != {name:wanted[name] for name in columns}:
                audit.fail(label+' attributes',identity)
            db.execute('UPDATE attrs SET seen=seen+1 WHERE kind=? AND id=?',(kind,identity))
    for (identity,) in db.execute('SELECT id FROM attrs WHERE kind=? AND seen=0',(kind,)):
        audit.fail(label+' missing identity',identity)
    return count


def check_database_rows(connection, db, audit):
    for table,key,columns in DIMENSIONS:
        kind=table.removeprefix('dim_')
        audit.dimensions[kind]=compare_attrs(db,audit,kind,pages(connection,table,key,', '.join((key,*columns))),columns,table)
    for kind,columns in (('customer',CUSTOMER_COLUMNS),('product',PRODUCT_COLUMNS)):
        compare_attrs(db,audit,kind,pages(connection,kind+'_master_data',columns[0],', '.join(columns)),columns,kind+' master')
    tables=('fact_sales f LEFT JOIN dim_customer c ON f.customer_key=c.customer_key '
            'LEFT JOIN dim_product p ON f.product_key=p.product_key '
            'LEFT JOIN dim_supplier s ON f.supplier_key=s.supplier_key '
            'LEFT JOIN dim_store st ON f.store_key=st.store_key LEFT JOIN dim_time t ON f.time_key=t.time_key')
    columns=('f.sales_key,f.source_line_id,f.transaction_id,c.customer_id,p.product_id,s.supplier_id,'
             'st.store_id,t.event_time,f.quantity,f.revenue,f.sales_mode,f.processing_mode')
    audit.actual=totals()
    for row in pages(connection,tables,'f.sales_key',columns):
        identity=row.pop('source_line_id')
        row.pop('sales_key')
        expected=db.execute('SELECT payload,seen FROM source WHERE id=?',(identity,)).fetchone()
        if expected is None:
            audit.fail('unexpected fact source ID',identity)
        else:
            if expected[1]:
                audit.fail('duplicate fact source ID',identity)
            wanted=json.loads(expected[0])['fact']
            actual=normalized(row)
            if actual != wanted:
                fields=','.join(k for k in wanted if actual.get(k)!=wanted[k])
                audit.fail('fact fields',f'{identity}: {fields}')
            db.execute('UPDATE source SET seen=seen+1 WHERE id=?',(identity,))
        try:
            add_total(audit.actual,row['sales_mode'],int(row['quantity']),Decimal(str(row['revenue'])))
        except (TypeError,ValueError,ArithmeticError):
            audit.fail('invalid fact measures',identity)
    for (identity,) in db.execute('SELECT id FROM source WHERE seen=0'):
        audit.fail('missing fact source ID',identity)
    for mode in audit.expected:
        if audit.expected[mode]!=audit.actual[mode]:
            audit.fail('channel totals',mode)
    db.execute('UPDATE source SET seen=0')
    for row in pages(connection,'periodic_sales_data','source_line_id',', '.join(PERIODIC_COLUMNS)):
        identity=row['source_line_id']
        expected=db.execute('SELECT payload,mode,release_hour,seen FROM source WHERE id=?',(identity,)).fetchone()
        if expected is None or expected[1]!='Periodic':
            audit.fail('unexpected staging source ID',identity)
            continue
        wanted=json.loads(expected[0])['staging']
        wanted.update(release_hour=str(expected[2]),released='1')
        if normalized(row)!=wanted:
            audit.fail('staging fields/hour/released flag',identity)
        if expected[3]:
            audit.fail('duplicate staging source ID',identity)
        db.execute('UPDATE source SET seen=seen+1 WHERE id=?',(identity,))
    for (identity,) in db.execute("SELECT id FROM source WHERE mode='Periodic' AND seen=0"):
        audit.fail('missing staging source ID',identity)
    for table in ('shed_table','periodic_join_results'):
        cursor=connection.cursor(dictionary=True)
        try:
            cursor.execute(f'SELECT COUNT(*) AS pending FROM {table}')
            count=cursor.fetchall()[0]['pending']
        finally:
            cursor.close()
        if count:
            audit.fail('pending '+table,count)
    from retail_dw.warehouse import online_cache_count
    count = online_cache_count(connection)
    if count:
        audit.fail('pending online_sales_cache', count)


def reconcile_warehouse(config, data_dir, progress=print):
    data_dir=Path(data_dir).resolve()
    audit=Audit()
    # Disk-backed expectations avoid retaining full masters or facts in memory.
    with localcontext() as context, TemporaryDirectory(prefix='reconciliation_',dir=data_dir) as folder:
        context.prec=50
        with closing(sqlite3.connect(str(Path(folder)/'expected.db'))) as db:
            progress('Validating source files and reconstructing expected attributes on disk...')
            try:
                build_expected(db,data_dir,audit)
            except (InvalidOperation, OverflowError) as error:
                raise ValueError('Source contains invalid or unrepresentable numeric values.') from error
            for kind,columns in (('customer',CUSTOMER_COLUMNS),('product',PRODUCT_COLUMNS)):
                compare_attrs(db,audit,kind,csv_rows(data_dir/(kind+'_master_data.csv'),columns),columns,kind+' master CSV')
            progress('Comparing all warehouse facts, dimensions, masters, and staging in 500-row pages...')
            with database_connection(config) as connection:
                connection.start_transaction(isolation_level='REPEATABLE READ',consistent_snapshot=True,readonly=True)
                try:
                    check_database_rows(connection,db,audit)
                finally:
                    connection.rollback()
    return audit
