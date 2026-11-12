"""Required project defaults and validated runtime settings."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeSettings:
    peak_rate: int = 80
    non_peak_rate: int = 10
    seconds_per_hour: int = 60
    arrival_mode: str = "non-peak"
    stream_buffer_limit: int = 1000
    stream_chunk_size: int = 500
    master_page_size: int = 500
    periodic_groups: int = 48

    def __post_init__(self):
        for name in ("peak_rate", "non_peak_rate", "seconds_per_hour"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        for name, required in (
            ("stream_buffer_limit", 1000), ("stream_chunk_size", 500),
            ("master_page_size", 500), ("periodic_groups", 48),
        ):
            value = getattr(self, name)
            if type(value) is not int or value != required:
                raise ValueError(f"{name} must be {required} under the project settings.")
        if self.arrival_mode not in ("peak", "non-peak"):
            raise ValueError("Arrival mode must be peak or non-peak.")

    def print_summary(self):
        print(f"Arrival mode: {self.arrival_mode}")
        print(f"Peak rate: {self.peak_rate} transactions/second")
        print(f"Non-peak rate: {self.non_peak_rate} transactions/second")
        print(f"One simulated hour: {self.seconds_per_hour} seconds")
        print(f"Stream-buffer limit: {self.stream_buffer_limit} transactions")

