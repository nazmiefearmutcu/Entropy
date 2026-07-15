# src/entropy/engine/rate.py
from __future__ import annotations

from collections import deque


class RateMeter:
    """Sliding events/sec over window_s using a high-precision rolling queue of timestamps."""

    __slots__ = ("window_s", "window_ns", "timestamps")

    def __init__(self, window_s: int) -> None:
        self.window_s = window_s
        self.window_ns = window_s * 1_000_000_000
        self.timestamps: deque[int] = deque()

    @property
    def total(self) -> int:
        return len(self.timestamps)

    def add(self, ts_ns: int, n: int = 1) -> None:
        for _ in range(n):
            self.timestamps.append(ts_ns)
        
        # Evict timestamps older than window_ns
        cutoff = ts_ns - self.window_ns
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()

    def rate_per_s(self) -> float:
        if not self.timestamps:
            return 0.0
        
        # Calculate time span in seconds
        elapsed = (self.timestamps[-1] - self.timestamps[0]) / 1_000_000_000
        span = elapsed + 1.0  # Add 1.0 to align with discrete bucket duration logic
        return len(self.timestamps) / span
