# src/entropy/engine/breadth.py
from __future__ import annotations

from .rate import RateMeter


class BreadthTracker:
    def __init__(self, window_s: int = 30, accel_eps: float = 0.10) -> None:
        self.window_s = window_s
        self.accel_eps = accel_eps
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self._tick_meter = RateMeter(window_s=1)     # raw Hz
        self._event_meter = RateMeter(window_s=window_s)

    def add_trade(self, side: str, amount: float, ts_ns: int) -> None:
        if side == "sell":
            self.sell_vol += amount
        else:
            self.buy_vol += amount

    def tick(self, ts_ns: int) -> None:
        self._tick_meter.add(ts_ns)

    def events(self, ts_ns: int, n: int) -> None:
        if n:
            self._event_meter.add(ts_ns, n)

    def sell_pct(self) -> float:
        tot = self.buy_vol + self.sell_vol
        return self.sell_vol / tot * 100 if tot else 0.0

    def buy_pct(self) -> float:
        return 100.0 - self.sell_pct() if (self.buy_vol + self.sell_vol) else 0.0

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
