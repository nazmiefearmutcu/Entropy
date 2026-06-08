# src/entropy/engine/breadth.py
from __future__ import annotations

from collections import deque

from .rate import RateMeter


class VolumeMeter:
    """Sliding notional-volume sum over window_s using 1-second buckets. O(1) add.

    Structurally identical to RateMeter but accumulates float amounts, so Sell%/Buy%
    reflect the recent window rather than the (ever-converging) session total.
    """

    __slots__ = ("window_s", "buckets", "total")

    def __init__(self, window_s: int) -> None:
        self.window_s = window_s
        self.buckets: deque[list[float]] = deque()  # [sec, amount_sum]
        self.total = 0.0

    def add(self, ts_ns: int, amount: float) -> None:
        sec = ts_ns // 1_000_000_000
        if self.buckets and self.buckets[-1][0] == sec:
            self.buckets[-1][1] += amount
        else:
            self.buckets.append([float(sec), amount])
        self.total += amount
        self.evict(ts_ns)

    def evict(self, now_ns: int) -> None:
        cutoff = now_ns // 1_000_000_000 - self.window_s
        while self.buckets and self.buckets[0][0] < cutoff:
            self.total -= self.buckets.popleft()[1]

    def volume(self, now_ns: int) -> float:
        # Evict on read too: a side that stops trading must still expire its
        # stale volume relative to the latest tick, not linger forever.
        self.evict(now_ns)
        return self.total


class BreadthTracker:
    def __init__(self, window_s: int = 30, accel_eps: float = 0.10) -> None:
        self.window_s = window_s
        self.accel_eps = accel_eps
        self._buy_vol = VolumeMeter(window_s)
        self._sell_vol = VolumeMeter(window_s)
        self._tick_meter = RateMeter(window_s=1)     # raw Hz
        self._event_meter = RateMeter(window_s=window_s)
        self._last_ts = 0

    def add_trade(self, side: str, amount: float, ts_ns: int) -> None:
        self._last_ts = max(self._last_ts, ts_ns)
        if side == "sell":
            self._sell_vol.add(ts_ns, amount)
        else:
            self._buy_vol.add(ts_ns, amount)

    def tick(self, ts_ns: int) -> None:
        self._last_ts = max(self._last_ts, ts_ns)
        self._tick_meter.add(ts_ns)

    def events(self, ts_ns: int, n: int) -> None:
        if n:
            self._event_meter.add(ts_ns, n)

    def sell_pct(self) -> float:
        s = self._sell_vol.volume(self._last_ts)
        tot = self._buy_vol.volume(self._last_ts) + s
        return s / tot * 100 if tot else 0.0

    def buy_pct(self) -> float:
        b = self._buy_vol.volume(self._last_ts)
        tot = b + self._sell_vol.volume(self._last_ts)
        return b / tot * 100 if tot else 0.0

    def raw_hz(self) -> float:
        return self._tick_meter.rate_per_s()

    def event_rate(self) -> float:
        return self._event_meter.rate_per_s()

    def accel(self, prev_rate: float) -> str:
        now = self.event_rate()
        if now > prev_rate * (1 + self.accel_eps):
            return "accelerating"
        if now < prev_rate * (1 - self.accel_eps):
            return "decelerating"
        return "steady"
