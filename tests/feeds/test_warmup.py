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

        async def close(self):  # the fetch path now closes the client it opened
            pass

    monkeypatch.setattr(yclient, "YahooClient", FakeClient)
    bars = await warmup_equity_bars("AAPL", "15m")
    assert [b.close for b in bars] == [42.0]


# --- client lifecycle: every warmup fetch closes the YahooClient it opened ----

class _LifecycleClient:
    """Fake YahooClient recording close(); behavior injected per test."""

    instances: list["_LifecycleClient"] = []

    def __init__(self) -> None:
        self.closed = False
        _LifecycleClient.instances.append(self)

    async def fetch_intraday_bars(self, symbol, interval, start=None, end=None):
        return await type(self)._behavior(symbol, interval)  # type: ignore[attr-defined]

    async def close(self) -> None:
        self.closed = True


def _patch_client(monkeypatch, behavior):
    import stockodile.providers.yahoo.client as yclient

    _LifecycleClient.instances = []
    _LifecycleClient._behavior = staticmethod(behavior)  # type: ignore[attr-defined]
    monkeypatch.setattr(yclient, "YahooClient", _LifecycleClient)
    return _LifecycleClient.instances


async def test_warmup_closes_client_on_success(monkeypatch):
    async def ok(symbol, interval):
        return [_stk(10, 1.0)]

    instances = _patch_client(monkeypatch, ok)
    await warmup_equity_bars("AAPL", "15m")
    assert [c.closed for c in instances] == [True]


async def test_warmup_closes_client_on_fetch_error(monkeypatch):
    async def boom(symbol, interval):
        raise RuntimeError("yahoo down")

    instances = _patch_client(monkeypatch, boom)
    with pytest.raises(RuntimeError):
        await warmup_equity_bars("AAPL", "15m")
    assert [c.closed for c in instances] == [True]


async def test_warmup_closes_client_on_timeout(monkeypatch):
    import asyncio

    async def hang(symbol, interval):
        await asyncio.sleep(30)
        return []

    instances = _patch_client(monkeypatch, hang)
    monkeypatch.setattr("entropy.feeds.warmup._YAHOO_TIMEOUT_S", 0.05)
    with pytest.raises(asyncio.TimeoutError):
        await warmup_equity_bars("AAPL", "15m")
    assert [c.closed for c in instances] == [True]


def test_warmup_timeout_covers_yahoo_internal_backoff():
    # YahooClient retries 429s with a 5s -> 10s -> 20s internal backoff; a 10s
    # outer timeout guaranteed spurious fallback after a single 429.
    from entropy.feeds import warmup

    assert warmup._YAHOO_TIMEOUT_S == 30.0


# --- "4h" interval mapping: yfinance has no 4h — fetch 1h and aggregate -------

_H = 3_600_000_000_000  # 1h in ns


async def test_warmup_equity_bars_4h_fetches_1h_and_aggregates(monkeypatch):
    # 8 consecutive 1h bars = exactly two 4h buckets. Aggregate: h=max, l=min,
    # c=last close, ts=4h bucket boundary.
    rows = [
        StkBar(provider="yahoo", symbol="SPY", symbol_raw="SPY", local_ts=1,
               interval="1h", open=100.0 + i, high=110.0 + i, low=90.0 - i,
               close=101.0 + i, volume=10.0, source_ts=i * _H)
        for i in range(8)
    ]
    calls = _patch_fetch(monkeypatch, rows)
    bars = await warmup_equity_bars("SPY", "4h")
    assert calls == [("SPY", "1h")]              # bug: asks yahoo for "4h"
    assert [b.ts_ns for b in bars] == [0, 4 * _H]
    assert bars[0].high == 113.0 and bars[0].low == 87.0 and bars[0].close == 104.0
    assert bars[1].high == 117.0 and bars[1].low == 83.0 and bars[1].close == 108.0


async def test_warmup_equity_bars_4h_partial_bucket_and_limit(monkeypatch):
    # 6 bars -> full bucket [0..3] + partial [4,5]; limit keeps the newest.
    rows = [
        StkBar(provider="yahoo", symbol="SPY", symbol_raw="SPY", local_ts=1,
               interval="1h", open=1.0, high=10.0 + i, low=5.0, close=float(i),
               volume=1.0, source_ts=i * _H)
        for i in range(6)
    ]
    _patch_fetch(monkeypatch, rows)
    bars = await warmup_equity_bars("SPY", "4h", limit=1)
    assert [b.ts_ns for b in bars] == [4 * _H]   # newest (partial) 4h bucket only
    assert bars[0].high == 15.0 and bars[0].close == 5.0


async def test_warmup_equity_bars_4h_filters_on_fetched_interval(monkeypatch):
    # Rows are filtered against the FETCHED interval (1h), not "4h".
    rows = [
        StkBar(provider="yahoo", symbol="SPY", symbol_raw="SPY", local_ts=1,
               interval="1h", open=1.0, high=2.0, low=0.5, close=1.5,
               volume=1.0, source_ts=0),
        StkBar(provider="yahoo", symbol="SPY", symbol_raw="SPY", local_ts=1,
               interval="1d", open=1.0, high=9.0, low=0.1, close=5.0,
               volume=1.0, source_ts=_H),
    ]
    _patch_fetch(monkeypatch, rows)
    bars = await warmup_equity_bars("SPY", "4h")
    assert len(bars) == 1 and bars[0].high == 2.0  # the 1d row was excluded
