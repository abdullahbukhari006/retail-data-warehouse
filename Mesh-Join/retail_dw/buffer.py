"""One bounded buffer with stable online, periodic, and replay priorities."""

from heapq import heappop, heappush, heapify
from itertools import count
from queue import Queue


class PriorityStreamBuffer(Queue):
    def _init(self, maxsize):
        self.queue = []
        self.sequence = count()

    def put(self, transaction, block=True, timeout=None):
        priority = 0 if transaction.sales_mode == "Online" else 1
        super().put((priority, transaction), block, timeout)

    def put_replay_nowait(self, transaction):
        if transaction.sales_mode != "Periodic":
            raise ValueError("Only periodic transactions may be replayed from shedding.")
        super().put((2, transaction), block=False)

    def _put(self, item):
        priority, transaction = item
        heappush(self.queue, (priority, next(self.sequence), transaction))

    def _get(self):
        return heappop(self.queue)[2]

    def shed_periodic(self, store, limit=500):
        """Called by the sole consumer. Persist before freeing any queue slot."""
        with self.mutex:
            if len(self.queue) != self.maxsize:
                return 0
            candidates = [entry for entry in sorted(self.queue) if entry[0] == 1][:limit]
        if not candidates:
            return 0
        store.persist([entry[2] for entry in candidates])
        sequences = {entry[1] for entry in candidates}
        with self.mutex:
            # Producers may append while persistence runs. The sole consumer
            # owns extraction, so these candidates cannot have been admitted.
            self.queue = [entry for entry in self.queue if entry[1] not in sequences]
            heapify(self.queue)
            self.unfinished_tasks -= len(candidates)
            if not self.unfinished_tasks:
                self.all_tasks_done.notify_all()
            self.not_full.notify_all()
        return len(candidates)
