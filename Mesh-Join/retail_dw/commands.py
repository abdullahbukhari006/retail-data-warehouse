"""Operator command parsing and status without sharing database connections."""

from queue import Queue
from threading import Thread


HELP = ("Commands: online, periodic, both, peak [rate], non-peak [rate], "
        "status, help, quit. Rates are positive transactions/second.")


class OperatorConsole:
    def __init__(self, control, buffer, read=input, write=print):
        self.control = control
        self.buffer = buffer
        self.read = read
        self.write = write
        self.inbox = Queue()
        self.reader = None

    def start(self):
        self.write(HELP)
        # Only this thread reads stdin. All commands execute in the coordinator.
        # Daemon ownership lets a database failure exit while stdin is blocked.
        self.reader = Thread(target=self._read, name="OperatorInput", daemon=True)
        self.reader.start()

    def _read(self):
        while True:
            try:
                line = self.read("operator> ")
            except EOFError:
                self.inbox.put("quit")
                return
            self.inbox.put(line)
            if line.strip().lower() == "quit":
                return

    def status(self):
        state = self.control.operator_snapshot()
        stats = self.control.snapshot()
        rate = state['peak_rate'] if state['speed'] == 'peak' else state['non_peak_rate']
        phase = "complete" if stats.get("workers_complete") else "running"
        self.write(
            f"Mode: {state['mode']} | Speed: {state['speed']} ({rate:,}/second) | "
            f"Rates peak/non-peak: {state['peak_rate']}/{state['non_peak_rate']} | "
            f"State: {phase}\n"
            f"Stream buffer: {self.buffer.qsize()}/{self.control.settings.stream_buffer_limit} | "
            f"H transactions: {stats.get('hash_transactions', 0):,} | "
            f"Queue chunks: {stats.get('queue_chunks', 0):,} | "
            f"Shed disk: {stats['shed_pending']:,}\n"
            f"Online submitted/enriched: {stats['online_submitted']:,}/{stats['enriched_online']:,} | "
            f"Periodic submitted/enriched: {stats['periodic_submitted']:,}/{stats['enriched_periodic']:,} | "
            f"Hourly groups: {stats['groups_released']}\n"
            "Counts reflect the latest completed worker update; accepted work continues to drain."
        )

    def execute(self, line):
        words = line.strip().lower().split()
        if not words:
            return
        command = words[0]
        if command in ("peak", "non-peak") and len(words) in (1, 2):
            try:
                rate = int(words[1]) if len(words) == 2 else None
                self.control.set_speed(command, rate)
            except ValueError:
                self.write("Enter a positive integer rate, for example peak 80.")
                return
        elif len(words) != 1:
            self.write(HELP)
            return
        elif command in ("online", "periodic", "both"):
            self.control.set_mode(command)
        elif command == "quit":
            self.control.stop.set()
            self.control.set_speed("non-peak")
            self.write("Stopping input and draining accepted work before closing connections.")
            return
        elif command == "help":
            self.write(HELP)
            return
        elif command != "status":
            self.write(HELP)
            return
        self.status()

    def poll(self):
        while not self.inbox.empty() and not self.control.stop.is_set():
            self.execute(self.inbox.get_nowait())
