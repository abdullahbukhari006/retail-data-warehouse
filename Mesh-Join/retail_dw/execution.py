"""Shared cancellation, rate control, and worker error reporting."""

from queue import Full, Queue
from threading import Condition, Event, Lock, Thread
from time import monotonic


class RunControl:
    def __init__(self, settings):
        self.settings = settings
        self.stop = Event()
        self.abort = Event()
        self.ready = Event()
        self.producers_done = Event()
        self.errors = Queue()
        self.rate_changed = Condition()
        self.arrival_mode = settings.arrival_mode
        self.processing_mode = "both"
        self.peak_rate = settings.peak_rate
        self.non_peak_rate = settings.non_peak_rate
        self.shedding_enabled = False
        self.stats_lock = Lock()
        self.stats = {
            "online_submitted": 0, "periodic_submitted": 0,
            "enriched_online": 0, "enriched_periodic": 0,
            "groups_released": 0,
            "shed_written": 0, "shed_replayed": 0, "shed_completed": 0,
            "shed_pending": 0, "shed_eligible_pending": 0,
        }

    def add_stats(self, **values):
        with self.stats_lock:
            for name, value in values.items():
                self.stats[name] = self.stats.get(name, 0) + value

    def set_stats(self, **values):
        with self.stats_lock:
            self.stats.update(values)

    def snapshot(self):
        with self.stats_lock:
            return dict(self.stats)

    def set_speed(self, mode, rate=None):
        if mode not in ("peak", "non-peak"):
            raise ValueError("Arrival mode must be peak or non-peak.")
        with self.rate_changed:
            if rate is not None:
                if not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0:
                    raise ValueError("Arrival rate must be a positive integer.")
                if mode == "peak":
                    self.peak_rate = rate
                else:
                    self.non_peak_rate = rate
            self.arrival_mode = mode
            self.rate_changed.notify_all()

    def set_mode(self, mode):
        if mode not in ("online", "periodic", "both"):
            raise ValueError("Mode must be online, periodic, or both.")
        with self.rate_changed:
            self.processing_mode = mode
            self.rate_changed.notify_all()

    def operator_snapshot(self):
        with self.rate_changed:
            return dict(mode=self.processing_mode, speed=self.arrival_mode,
                        peak_rate=self.peak_rate, non_peak_rate=self.non_peak_rate)

    def wait_for_channel(self, channel):
        with self.rate_changed:
            while not self.stop.is_set():
                if self.processing_mode in ("both", channel):
                    return True
                self.rate_changed.wait(0.1)
        return False

    def speed(self):
        with self.rate_changed:
            return self.arrival_mode

    def wait_for_arrival(self, previous):
        with self.rate_changed:
            while not self.stop.is_set():
                if self.processing_mode == "periodic":
                    self.rate_changed.wait(0.1)
                    continue
                rate = self.peak_rate if self.arrival_mode == "peak" else self.non_peak_rate
                remaining = 0 if previous is None else previous + 1 / rate - monotonic()
                if remaining <= 0:
                    return True
                self.rate_changed.wait(min(remaining, 0.1))
        return False

    def submit(self, buffer, transaction):
        while not self.stop.is_set():
            if transaction.sales_mode == "Online" and not self.wait_for_channel("online"):
                return False
            try:
                buffer.put(transaction, timeout=0.1)
                return True
            except Full:
                pass
        return False


class WorkerThread(Thread):
    def __init__(self, name, control):
        super().__init__(name=name)
        self.control = control
        self.finished = Event()

    def run(self):
        try:
            self.work()
        except BaseException as error:
            self.control.errors.put((self.name, error))
            self.control.abort.set()
            self.control.stop.set()
        finally:
            self.finished.set()

    def work(self):
        raise NotImplementedError
