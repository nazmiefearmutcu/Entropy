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
        """Honest events/sec over the observed span.

        Steady state (observed span has filled the window): count / window_s —
        the deque holds exactly the last window_s of stamps. Warm-up (span still
        shorter than the window): count / elapsed with a 1s floor, so a
        sub-second burst reads as events-per-that-second instead of exploding
        as elapsed -> 0.
        """
        if not self.timestamps:
            return 0.0
        elapsed = (self.timestamps[-1] - self.timestamps[0]) / 1_000_000_000
        if elapsed >= self.window_s:
            return len(self.timestamps) / self.window_s
        return len(self.timestamps) / max(elapsed, 1.0)
