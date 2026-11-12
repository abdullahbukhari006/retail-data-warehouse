"""Transactional persistence and acknowledgement of shed periodic lines."""

from dataclasses import fields
from queue import Full

from retail_dw.database import database_transaction
from retail_dw.meshjoin import SalesTransaction


SHED_COLUMNS = tuple(field.name for field in fields(SalesTransaction))


class ShedStore:
    def __init__(self, connection, control, hours=48):
        self.connection = connection
        self.control = control
        self.hours = hours
        self.inflight = set()

    def counts(self):
        with database_transaction(self.connection):
            cursor = self.connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN "
                    "p.release_hour < %s AND p.released = 0 THEN 1 ELSE 0 END), 0) AS eligible "
                    "FROM shed_table s LEFT JOIN periodic_sales_data p "
                    "ON p.source_line_id = s.source_line_id", (self.hours,),
                )
                row = cursor.fetchall()[0]
                return int(row["total"]), int(row["eligible"])
            finally:
                cursor.close()

    def persist(self, transactions):
        if not transactions:
            return
        if any(transaction.sales_mode != "Periodic" for transaction in transactions):
            raise ValueError("Only periodic transactions may be shed.")
        ids = tuple(transaction.source_line_id for transaction in transactions)
        marks = ', '.join(['%s'] * len(ids))
        with database_transaction(self.connection):
            cursor = self.connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    f"SELECT {', '.join(SHED_COLUMNS)} FROM shed_table "
                    f"WHERE source_line_id IN ({marks}) FOR UPDATE", ids,
                )
                existing = {row["source_line_id"]: SalesTransaction.from_row(row) for row in cursor.fetchall()}
                fresh = []
                for transaction in transactions:
                    previous = existing.get(transaction.source_line_id)
                    if previous is not None and previous != transaction:
                        raise ValueError("Shed source-line identity conflicts with its stored transaction.")
                    if previous is None:
                        fresh.append(tuple(getattr(transaction, name) for name in SHED_COLUMNS))
                if fresh:
                    cursor.executemany(
                        f"INSERT INTO shed_table ({', '.join(SHED_COLUMNS)}) "
                        f"VALUES ({', '.join(['%s'] * len(SHED_COLUMNS))})", fresh,
                    )
            finally:
                cursor.close()

    def replay(self, buffer, limit=500):
        hour = self.control.snapshot().get("current_periodic_hour")
        if hour is None:
            return 0
        capacity = min(limit, buffer.maxsize - buffer.qsize())
        if capacity <= 0:
            return 0
        parameters = [hour]
        statement = (
            f"SELECT {', '.join('s.' + name for name in SHED_COLUMNS)} "
            "FROM shed_table s JOIN periodic_sales_data p ON p.source_line_id = s.source_line_id "
            "WHERE p.release_hour = %s AND p.released = 0"
        )
        if self.inflight:
            statement += f" AND s.source_line_id NOT IN ({', '.join(['%s'] * len(self.inflight))})"
            parameters.extend(sorted(self.inflight))
        statement += " ORDER BY s.shed_at, s.source_line_id LIMIT %s"
        parameters.append(capacity)
        with database_transaction(self.connection):
            cursor = self.connection.cursor(dictionary=True)
            try:
                cursor.execute(statement, tuple(parameters))
                rows = cursor.fetchall()
            finally:
                cursor.close()
        replayed = 0
        for row in rows:
            transaction = SalesTransaction.from_row(row)
            try:
                buffer.put_replay_nowait(transaction)
            except Full:
                break
            self.inflight.add(transaction.source_line_id)
            replayed += 1
        return replayed

    def acknowledge(self, source_ids):
        ids = tuple(source_ids)
        if not ids:
            return 0
        with database_transaction(self.connection):
            cursor = self.connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    f"DELETE FROM shed_table WHERE source_line_id IN ({', '.join(['%s'] * len(ids))})", ids,
                )
                removed = cursor.rowcount
            finally:
                cursor.close()
        self.inflight.difference_update(ids)
        return removed
