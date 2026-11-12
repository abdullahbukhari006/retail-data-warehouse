"""Bounded, staggered MESHJOIN over two cyclic disk-backed master scans."""

from collections import deque
from contextlib import ExitStack, closing
import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from queue import Empty
from types import MappingProxyType
from typing import Mapping

from retail_dw.database import database_connection
from retail_dw.buffer import PriorityStreamBuffer
from retail_dw.execution import WorkerThread
from retail_dw.prepare_data import CUSTOMER_COLUMNS, GENERATED_COLUMNS, PRODUCT_COLUMNS
from retail_dw.settings import RuntimeSettings


MASTER_COLUMNS = {
    "customer_master_data": CUSTOMER_COLUMNS,
    "product_master_data": PRODUCT_COLUMNS,
}


@dataclass(frozen=True, slots=True)
class SalesTransaction:
    source_line_id: int
    transaction_id: str
    customer_id: str
    product_id: str
    quantity: int
    unit_price: Decimal
    event_time: datetime
    sales_mode: str
    processing_mode: str

    def __post_init__(self):
        if type(self.source_line_id) is not int or self.source_line_id < 2:
            raise ValueError("A transaction needs its original workbook row ID.")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("Transaction quantity must be a positive integer.")
        if not isinstance(self.unit_price, Decimal) or not self.unit_price.is_finite() or self.unit_price <= 0:
            raise ValueError("Transaction unit price must be a positive finite Decimal.")
        for name in ("transaction_id", "customer_id", "product_id", "processing_mode"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Transaction {name} must be nonempty text.")
        if self.sales_mode not in ("Online", "Periodic"):
            raise ValueError("Transaction sales mode must be Online or Periodic.")
        if not isinstance(self.event_time, datetime) or self.event_time.tzinfo is not None:
            raise ValueError("Transaction event time must be a timezone-naive datetime.")

    @classmethod
    def from_row(cls, row):
        required = (
            "source_line_id", "transaction_id", "customer_id", "product_id",
            "quantity", "unit_price", "event_time", "sales_mode",
        )
        if any(row.get(name) is None for name in required):
            raise ValueError("Transaction row is missing a required field.")
        try:
            price = Decimal(str(row["unit_price"]))
            quantity = Decimal(str(row["quantity"]))
            source_id = Decimal(str(row["source_line_id"]))
        except InvalidOperation as error:
            raise ValueError("Transaction source ID, quantity, and price must be numeric.") from error
        if (not quantity.is_finite() or quantity != quantity.to_integral_value()
                or not source_id.is_finite() or source_id != source_id.to_integral_value()):
            raise ValueError("Transaction source ID and quantity must be finite integers.")
        default_mode = "Live" if row["sales_mode"] == "Online" else "Periodic"
        event_time = row["event_time"]
        if isinstance(event_time, str):
            event_time = datetime.fromisoformat(event_time)
        return cls(
            source_line_id=int(source_id),
            transaction_id=row["transaction_id"], customer_id=row["customer_id"],
            product_id=row["product_id"], quantity=int(quantity),
            unit_price=price, event_time=event_time,
            sales_mode=row["sales_mode"],
            processing_mode=row.get("processing_mode", default_mode),
        )


@dataclass(frozen=True, slots=True)
class EnrichedTransaction:
    transaction: SalesTransaction
    customer: Mapping[str, object]
    product: Mapping[str, object]


@dataclass(slots=True)
class PendingTransaction:
    transaction: SalesTransaction
    customer: Mapping[str, object] | None = None
    product: Mapping[str, object] | None = None


@dataclass(slots=True)
class Chunk:
    # These point to the same PendingTransaction objects indexed in H.
    members: tuple[PendingTransaction, ...]
    iterations: int = 0


class MasterPageReader:
    """Read one ordered page per call, wrapping after the actual last page.

    Create and use this reader inside the connection's owning thread.
    Master tables are prepared before processing and stay fixed during scans.
    """

    def __init__(self, connection, table, page_size=500):
        if table not in MASTER_COLUMNS:
            raise ValueError("Unsupported MESHJOIN master table.")
        if page_size <= 0:
            raise ValueError("Master page size must be positive.")
        self.table = table
        self.columns = MASTER_COLUMNS[table]
        self.key = self.columns[0]
        self.page_size = page_size
        self.page_index = 0
        self.last_key = None
        self.pages_read = 0
        self.cursor = connection.cursor(dictionary=True)
        try:
            self.cursor.execute(f"SELECT COUNT(*) AS tuple_count FROM {table}")
            self.row_count = self.cursor.fetchall()[0]["tuple_count"]
            if self.row_count == 0:
                raise ValueError(f"Master Data R is empty: {table}. Load masters first.")
            self.partition_count = (self.row_count + page_size - 1) // page_size
        except BaseException:
            self.cursor.close()
            raise

    def read_next_page(self):
        statement = f"SELECT {', '.join(self.columns)} FROM {self.table}"
        parameters = []
        if self.last_key is not None:
            statement += f" WHERE {self.key} > %s"
            parameters.append(self.last_key)
        statement += f" ORDER BY {self.key} LIMIT %s"
        parameters.append(self.page_size)
        self.cursor.execute(statement, tuple(parameters))
        page = self.cursor.fetchall()
        expected = min(self.page_size, self.row_count - self.page_index * self.page_size)
        if len(page) != expected:
            raise ValueError(f"Master Data R changed during its scan: {self.table}.")
        self.pages_read += 1
        self.page_index += 1
        self.last_key = page[-1][self.key]
        if self.page_index == self.partition_count:
            self.page_index = 0
            self.last_key = None
        return page

    def close(self):
        self.cursor.close()


class MeshJoin:
    """Advance both masters once per iteration and admit up to one chunk.

    The output callback receives one fully enriched chunk at a time. It must
    complete synchronously and use an atomic or idempotent downstream write.
    If it raises, the chunk stays in H and Queue. The next step retries the
    delivery before reading pages or admitting more transactions.
    """

    def __init__(self, customer_reader, product_reader, on_chunk, settings=None, stream_buffer=None, before_admission=None):
        self.settings = settings or RuntimeSettings()
        self.readers = {"customer": customer_reader, "product": product_reader}
        self.on_chunk = on_chunk
        self.before_admission = before_admission
        self.stream_buffer = stream_buffer if stream_buffer is not None else PriorityStreamBuffer(maxsize=self.settings.stream_buffer_limit)
        if self.stream_buffer.maxsize != self.settings.stream_buffer_limit:
            raise ValueError("The shared Stream buffer must have the configured capacity.")
        # Join key -> {source-line ID: actual pending transaction}. Both
        # customer and product buckets reference the same transaction object.
        self.H = {}
        self.queue = deque()
        self.disk_buffers = {"customer": [], "product": []}
        self.active_ids = set()
        self.pending_admission = []
        self.iterations = 0
        self.admitted = 0
        self.emitted = 0
        self.failed = False
        self.retention_iterations = max(reader.partition_count for reader in self.readers.values())
        if any(reader.page_size != self.settings.master_page_size for reader in self.readers.values()):
            raise ValueError("Master readers must use the configured disk-buffer page size.")

    @property
    def active_count(self):
        return len(self.active_ids)

    @property
    def has_work(self):
        return bool(self.queue) or not self.stream_buffer.empty()

    def _admit_chunk(self):
        self.pending_admission = []
        for _ in range(self.settings.stream_chunk_size):
            try:
                self.pending_admission.append(self.stream_buffer.get_nowait())
            except Empty:
                break
        new_ids = set()
        for transaction in self.pending_admission:
            if not isinstance(transaction, SalesTransaction):
                raise ValueError("Stream buffer entries must be SalesTransaction objects.")
            source_id = transaction.source_line_id
            if source_id in self.active_ids or source_id in new_ids:
                raise ValueError(f"Duplicate active source-line ID: {source_id}.")
            new_ids.add(source_id)
        members = tuple(PendingTransaction(transaction) for transaction in self.pending_admission)
        for state in members:
            transaction = state.transaction
            for kind, key in (("customer", transaction.customer_id), ("product", transaction.product_id)):
                self.H.setdefault((kind, key), {})[transaction.source_line_id] = state
        if members:
            self.queue.appendleft(Chunk(members))
            self.active_ids.update(new_ids)
            self.admitted += len(members)
        self.pending_admission = []

    def _probe_page(self, kind):
        key_column = self.readers[kind].key
        for master in self.disk_buffers[kind]:
            for state in self.H.get((kind, master[key_column]), {}).values():
                if getattr(state, kind) is None:
                    setattr(state, kind, MappingProxyType(master))

    def _retire_ready_chunks(self):
        completed = 0
        while self.queue and self.queue[-1].iterations >= self.retention_iterations:
            chunk = self.queue[-1]
            missing = [state.transaction.source_line_id for state in chunk.members
                       if state.customer is None or state.product is None]
            if missing:
                self.failed = True
                raise ValueError(f"Full master scans left unmatched source lines: {missing[:5]}.")
            output = [EnrichedTransaction(state.transaction, state.customer, state.product)
                      for state in chunk.members]
            # Keep ownership until the callback confirms successful handoff.
            self.on_chunk(output)
            for state in chunk.members:
                transaction = state.transaction
                for kind, key in (("customer", transaction.customer_id), ("product", transaction.product_id)):
                    bucket = self.H[(kind, key)]
                    del bucket[transaction.source_line_id]
                    if not bucket:
                        del self.H[(kind, key)]
                self.active_ids.remove(transaction.source_line_id)
                self.stream_buffer.task_done()
            self.queue.pop()
            self.emitted += len(output)
            completed += len(output)
        return completed

    def step(self):
        if self.failed:
            raise ValueError("MESHJOIN stopped after an input or master-scan failure. Inspect retained state before restarting.")
        completed = self._retire_ready_chunks()
        if not self.has_work:
            return completed
        try:
            # Pages are replaced once per iteration. Incoming producers can
            # continue filling the bounded Stream buffer while these reads run.
            for kind, reader in self.readers.items():
                self.disk_buffers[kind] = reader.read_next_page()
            if self.before_admission is not None:
                self.before_admission()
            self._admit_chunk()
            for kind in self.readers:
                self._probe_page(kind)
            for chunk in self.queue:
                chunk.iterations += 1
            self.iterations += 1
        except BaseException:
            # A partial disk-read iteration must never count as full coverage.
            self.failed = True
            raise
        return completed + self._retire_ready_chunks()


class MeshJoinThread(WorkerThread):
    def __init__(self, config, buffer, on_chunk, control, on_ack=None, shedding=False, hours=48, warehouse=False):
        super().__init__("MeshJoinThread", control)
        self.config = config
        self.buffer = buffer
        self.on_chunk = on_chunk
        self.join_operator = None
        self.on_ack = on_ack
        self.shedding = shedding
        self.hours = hours
        self.warehouse = warehouse

    def work(self):
        settings = self.control.settings
        with database_connection(self.config) as connection, ExitStack() as stack:
            loader = None
            if self.warehouse:
                from retail_dw.warehouse import WarehouseLoader

                writer_connection = stack.enter_context(database_connection(self.config))
                loader = WarehouseLoader(writer_connection)
                loader.ensure_schema()
            store = None
            if self.shedding:
                from retail_dw.shedding import ShedStore

                shed_connection = stack.enter_context(database_connection(self.config))
                store = ShedStore(shed_connection, self.control, self.hours)

            def deliver(output):
                if loader is not None:
                    self.control.add_stats(**loader.load_chunk(output))
                self.on_chunk(output)
                ids = [row.transaction.source_line_id for row in output if row.transaction.sales_mode == "Periodic"]
                if store is not None:
                    self.control.add_stats(shed_completed=store.acknowledge(ids))
                if self.on_ack is not None:
                    self.on_ack(ids)

            def relieve_pressure():
                if store is not None and self.control.speed() == "peak" and self.join_operator.active_count:
                    self.control.add_stats(shed_written=self.buffer.shed_periodic(store))

            customer = stack.enter_context(closing(MasterPageReader(connection, "customer_master_data", settings.master_page_size)))
            product = stack.enter_context(closing(MasterPageReader(connection, "product_master_data", settings.master_page_size)))
            self.join_operator = MeshJoin(customer, product, deliver, settings, self.buffer, relieve_pressure)
            self.control.set_stats(customer_partitions=customer.partition_count,
                                   product_partitions=product.partition_count)
            self.control.ready.set()
            while not self.control.abort.is_set():
                eligible = 0
                if store is not None:
                    if self.control.speed() == "non-peak":
                        self.control.add_stats(shed_replayed=store.replay(self.buffer))
                    total, eligible = store.counts()
                    self.control.set_stats(shed_pending=total, shed_eligible_pending=eligible)
                if self.join_operator.has_work:
                    self.join_operator.step()
                    self.control.set_stats(
                        iterations=self.join_operator.iterations,
                        hash_transactions=self.join_operator.active_count,
                        queue_chunks=len(self.join_operator.queue),
                        customer_pages_read=customer.pages_read,
                        product_pages_read=product.pages_read,
                    )
                elif self.control.producers_done.is_set() and not eligible:
                    break
                else:
                    self.control.abort.wait(0.02)


def check_meshjoin(config, data_dir, limit=1000, settings=None):
    """Exercise the real paged database join without loading warehouse facts."""
    from pathlib import Path

    settings = settings or RuntimeSettings()
    if limit <= 0:
        raise ValueError("Join-check limit must be positive.")
    matched = 0

    def verify_chunk(output):
        nonlocal matched
        for enriched in output:
            transaction = enriched.transaction
            if (enriched.customer["customer_id"] != transaction.customer_id
                    or enriched.product["product_id"] != transaction.product_id):
                raise ValueError("Enriched transaction has an incorrect master match.")
        matched += len(output)

    with database_connection(config) as connection, ExitStack() as stack:
        customer = stack.enter_context(closing(MasterPageReader(connection, "customer_master_data", settings.master_page_size)))
        product = stack.enter_context(closing(MasterPageReader(connection, "product_master_data", settings.master_page_size)))
        join = MeshJoin(customer, product, verify_chunk, settings)
        source = stack.enter_context((Path(data_dir) / "assigned_sales.csv").open(encoding="utf-8", newline=""))
        rows = csv.DictReader(source)
        if rows.fieldnames != list(GENERATED_COLUMNS["assigned_sales.csv"]):
            raise ValueError("Unexpected assigned CSV columns. Run generate-data first.")
        exhausted = False
        submitted = 0
        while not exhausted or join.has_work:
            while not exhausted and not join.stream_buffer.full():
                row = next(rows, None) if submitted < limit else None
                if row is None:
                    exhausted = True
                    break
                join.stream_buffer.put_nowait(SalesTransaction.from_row(row))
                submitted += 1
            join.step()
        if matched != submitted or join.active_count or join.H:
            raise ValueError("MESHJOIN output did not reconcile with its admitted input.")
        return {
            "matched": matched, "iterations": join.iterations,
            "customer_partitions": customer.partition_count,
            "product_partitions": product.partition_count,
            "customer_pages_read": customer.pages_read,
            "product_pages_read": product.pages_read,
            "stream_buffer": join.stream_buffer.qsize(),
            "hash_transactions": join.active_count, "queue_chunks": len(join.queue),
        }
