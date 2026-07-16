# tests/feeds/test_warmup.py
import pytest
from stockodile.schema.records import Bar as StkBar

from entropy.feeds.warmup import bars_from_ohlcv, bars_from_stockodile, warmup_equity_bars
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


# --- stockodile Bar conversion + warmup_equity_bars (no network) --------------

def _stk(ts: int | None, close: float, *, local_ts: int = 7777,
         interval: str = "15m") -> StkBar:
    """Fabricated stockodile Bar (source_ts=ts, ns)."""
    return StkBar(provider="yahoo", symbol="AAPL", symbol_raw="AAPL",
                  local_ts=local_ts, interval=interval, open=close - 0.5,
                  high=close + 1.0, low=close - 1.0, close=close, volume=100.0,
                  source_ts=ts)


def _patch_fetch(monkeypatch, rows):
    """Route warmup_equity_bars' lazy Yahoo fetch to fabricated rows."""
    calls: list[tuple[str, str]] = []

    async def fake(symbol: str, interval: str):
        calls.append((symbol, interval))
        return rows

    monkeypatch.setattr("entropy.feeds.warmup._fetch_yahoo_bars", fake)
    return calls


def test_bars_from_stockodile_maps_ohlc_fields():
    bars = bars_from_stockodile([_stk(10, 2.0), _stk(20, 3.0)])
    assert bars == [Bar(ts_ns=10, close=2.0, high=3.0, low=1.0),
                    Bar(ts_ns=20, close=3.0, high=4.0, low=2.0)]


def test_bars_from_stockodile_prefers_source_ts_falls_back_to_local_ts():
    bars = bars_from_stockodile([_stk(5, 1.0, local_ts=99),
                                 _stk(None, 2.0, local_ts=99)])
    assert [b.ts_ns for b in bars] == [5, 99]


async def test_warmup_equity_bars_sorts_ascending(monkeypatch):
    calls = _patch_fetch(monkeypatch, [_stk(30, 3.0), _stk(10, 1.0), _stk(20, 2.0)])
    bars = await warmup_equity_bars("AAPL", "15m")
    assert [b.ts_ns for b in bars] == [10, 20, 30]
    assert calls == [("AAPL", "15m")]


async def test_warmup_equity_bars_limit_keeps_newest(monkeypatch):
    _patch_fetch(monkeypatch, [_stk(i * 10, float(i)) for i in range(1, 6)])
    bars = await warmup_equity_bars("AAPL", "15m", limit=3)
    assert [b.ts_ns for b in bars] == [30, 40, 50]  # newest 3, still ascending
    assert [b.close for b in bars] == [3.0, 4.0, 5.0]


async def test_warmup_equity_bars_filters_mixed_intervals(monkeypatch):
    _patch_fetch(monkeypatch, [_stk(10, 1.0, interval="15m"),
                               _stk(20, 2.0, interval="5m"),
                               _stk(30, 3.0, interval="15m")])
    bars = await warmup_equity_bars("AAPL", "15m")
    assert [b.ts_ns for b in bars] == [10, 30]


async def test_warmup_equity_bars_empty_raises(monkeypatch):
    _patch_fetch(monkeypatch, [])
    with pytest.raises(RuntimeError):
        await warmup_equity_bars("AAPL", "15m")


async def test_warmup_equity_bars_wrong_interval_only_raises(monkeypatch):
    _patch_fetch(monkeypatch, [_stk(10, 1.0, interval="1m")])
    with pytest.raises(RuntimeError):
        await warmup_equity_bars("AAPL", "15m")


async def test_warmup_equity_bars_patches_via_lazy_yahoo_client(monkeypatch):
    """The unpatched fetch path instantiates YahooClient lazily — patching the
    class inside stockodile's module must be enough to avoid the network."""
    import stockodile.providers.yahoo.client as yclient

    class FakeClient:
        async def fetch_intraday_bars(self, symbol, interval, start=None, end=None):
            assert (symbol, interval) == ("AAPL", "15m")
            return [_stk(10, 42.0)]

    monkeypatch.setattr(yclient, "YahooClient", FakeClient)
    bars = await warmup_equity_bars("AAPL", "15m")
    assert [b.close for b in bars] == [42.0]
