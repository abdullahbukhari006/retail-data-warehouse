"""Independent, rate-controlled reading of the required online CSV."""

import csv
from contextlib import closing
from itertools import islice
from pathlib import Path
from time import monotonic

from retail_dw.execution import WorkerThread
from retail_dw.meshjoin import SalesTransaction
from retail_dw.prepare_data import GENERATED_COLUMNS, ONLINE_COLUMNS


class OnlineThread(WorkerThread):
    def __init__(self, online_file, assigned_file, buffer, control, limit=None):
        super().__init__("OnlineThread", control)
        self.online_file = Path(online_file)
        self.assigned_file = Path(assigned_file)
        self.buffer = buffer
        self.limit = limit

    def transactions(self):
        # The required online format has no source ID. Walk its matching
        # assigned input in parallel, preserving repeated lines individually.
        with self.online_file.open(encoding="utf-8", newline="") as stream:
            with self.assigned_file.open(encoding="utf-8", newline="") as source:
                online = csv.DictReader(stream)
                assigned = csv.DictReader(source)
                if online.fieldnames != list(ONLINE_COLUMNS):
                    raise ValueError("Online CSV does not have the seven prescribed fields.")
                if assigned.fieldnames != list(GENERATED_COLUMNS["assigned_sales.csv"]):
                    raise ValueError("Unexpected assigned CSV fields for online identity mapping.")
                previous_id = 1
                for row in assigned:
                    if self.control.stop.is_set():
                        return
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("Malformed assigned transaction row.")
                    source_id = int(row["source_line_id"])
                    if source_id <= previous_id:
                        raise ValueError("Assigned source-line IDs must stay in workbook order.")
                    previous_id = source_id
                    if row["sales_mode"] != "Online":
                        continue
                    actual = next(online, None)
                    if actual is None or actual != {name: row[name] for name in ONLINE_COLUMNS}:
                        raise ValueError("Online CSV does not match assigned_sales.csv. Regenerate matching inputs.")
                    yield SalesTransaction.from_row({**actual, "source_line_id": source_id})
                if next(online, None) is not None:
                    raise ValueError("Online CSV has extra lines beyond its assigned input.")

    def work(self):
        previous = None
        with closing(self.transactions()) as transactions:
            for transaction in islice(transactions, self.limit):
                if not self.control.wait_for_arrival(previous):
                    break
                if not self.control.submit(self.buffer, transaction):
                    break
                previous = monotonic()
                self.control.add_stats(online_submitted=1)
