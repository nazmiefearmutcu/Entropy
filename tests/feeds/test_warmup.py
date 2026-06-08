# tests/feeds/test_warmup.py
from entropy.feeds.warmup import bars_from_ohlcv
from entropy.strategy.engine import Bar


class _O:   # duck-typed OHLCV
    def __init__(self, t: int, o: float, h: float, lo: float, c: float) -> None:
        self.exchange_ts = t
        self.local_ts = t
        self.open = o
        self.high = h
        self.low = lo
        self.close = c

def test_bars_from_ohlcv_maps_fields():
    bars = bars_from_ohlcv([_O(10, 1, 2, 0.5, 1.5), _O(20, 1.5, 3, 1, 2.5)])
    assert bars == [Bar(ts_ns=10, close=1.5, high=2, low=0.5),
                    Bar(ts_ns=20, close=2.5, high=3, low=1)]
