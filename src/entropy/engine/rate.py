# src/entropy/engine/rate.py
from __future__ import annotations

from collections import deque


class RateMeter:
    """Sliding events/sec over window_s using 1-second integer buckets. O(1) add."""

    __slots__ = ("window_s", "buckets", "total")

    def __init__(self, window_s: int) -> None:
        self.window_s = window_s
        self.buckets: deque[list[int]] = deque()  # [sec, count]
        self.total = 0

    def add(self, ts_ns: int, n: int = 1) -> None:
        sec = ts_ns // 1_000_000_000
        if self.buckets and self.buckets[-1][0] == sec:
            self.buckets[-1][1] += n
        else:
            self.buckets.append([sec, n])
        self.total += n
        cutoff = sec - self.window_s
        while self.buckets and self.buckets[0][0] < cutoff:
            self.total -= self.buckets.popleft()[1]

    def rate_per_s(self) -> float:
        if not self.buckets:
            return 0.0
        span = self.buckets[-1][0] - self.buckets[0][0] + 1
        return self.total / span
