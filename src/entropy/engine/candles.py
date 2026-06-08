from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class OHLCBar:
    t: int
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    vol: float


class CandleAggregator:
    """Builds rolling OHLCV bars for ONE symbol from live trades."""

    def __init__(self, interval_ns: int, maxlen: int = 120) -> None:
        self.interval_ns = interval_ns
        self._bars: deque[OHLCBar] = deque(maxlen=maxlen)
        self._cur_bucket = -1

    def add(self, ts_ns: int, price: float, amount: float) -> None:
        bucket = ts_ns // self.interval_ns
        if bucket != self._cur_bucket:
            self._bars.append(
                OHLCBar(bucket * self.interval_ns, price, price, price, price, amount)
            )
            self._cur_bucket = bucket
        else:
            b = self._bars[-1]
            if price > b.h:
                b.h = price
            if price < b.l:
                b.l = price
            b.c = price
            b.vol += amount

    def bars(self) -> list[OHLCBar]:
        return list(self._bars)
