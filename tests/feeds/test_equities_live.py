"""Tests for the stockodile-backed live equity feed (no network)."""
import asyncio

import pytest
from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade as CTrade
from crypcodile.sink.base import Sink
from stockodile.schema.records import Bar as SBar
from stockodile.schema.records import Quote as SQuote
from stockodile.schema.records import Trade as STrade

from entropy.feeds.equities import live
from entropy.feeds.equities.live import (
    RecordAdapterSink,
    build_equity_providers,
    start_equity_feed,
)
from entropy.feeds.equities.universe import LIVE_UNIVERSE, UNIVERSE


class CaptureSink(Sink):
    def __init__(self) -> None:
        self.records: list = []
        self.flushes = 0

    async def put(self, record) -> None:
        self.records.append(record)

    async def flush(self) -> None:
        self.flushes += 1


class ExplodingSink(Sink):
    async def put(self, record) -> None:
        raise RuntimeError("boom")

    async def flush(self) -> None:
        pass


def _stk_trade(**kw) -> STrade:
    base = dict(provider="google_finance", symbol="aapl", symbol_raw="aapl",
                local_ts=2_000, id="t1", price=213.5, size=100.0, source_ts=1_000)
    base.update(kw)
    return STrade(**base)


# ---------------------------------------------------------------- adapter map

async def test_adapter_maps_stockodile_trade():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_trade())
    assert adapter.errors == 0
    assert len(out.records) == 1
    tr = out.records[0]
    assert isinstance(tr, CTrade)
    assert tr.exchange == "stk-google_finance"   # provider prefixed
    assert tr.symbol == "AAPL"                   # bare upper ticker
    assert tr.symbol_raw == "aapl"
    assert tr.exchange_ts == 1_000               # source_ts preferred
    assert tr.local_ts == 2_000
    assert tr.id == "t1"
    assert tr.price == 213.5
    assert tr.amount == 100.0                    # size -> amount
    assert tr.side is Side.UNKNOWN               # stockodile Trade has no side


async def test_adapter_trade_ts_falls_back_to_local_ts():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_trade(source_ts=None))
    assert out.records[0].exchange_ts == 2_000


async def test_adapter_maps_bar_to_trade():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    bar = SBar(provider="stooq", symbol="spy", symbol_raw="spy", local_ts=5_000,
               interval="1d", open=1.0, high=2.0, low=0.5, close=1.5,
               volume=42.0, source_ts=4_000)
    await adapter.put(bar)
    tr = out.records[0]
    assert isinstance(tr, CTrade)
    assert tr.exchange == "stk-stooq"
    assert tr.symbol == "SPY"
    assert tr.price == 1.5                       # close
    assert tr.amount == 42.0                     # volume
    assert tr.exchange_ts == 4_000
    assert tr.side is Side.UNKNOWN


async def test_adapter_bar_zero_volume_maps_to_zero_amount():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    bar = SBar(provider="stooq", symbol="SPY", symbol_raw="SPY", local_ts=1,
               interval="1d", open=1.0, high=1.0, low=1.0, close=1.0, volume=0.0)
    await adapter.put(bar)
    assert out.records[0].amount == 0.0


async def test_adapter_ignores_other_record_types_silently():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    quote = SQuote(provider="finnhub", symbol="AAPL", symbol_raw="AAPL", local_ts=1,
                   bid_px=1.0, bid_sz=1.0, ask_px=1.1, ask_sz=1.0)
    await adapter.put(quote)
    assert out.records == []
    assert adapter.errors == 0


async def test_adapter_swallows_poison_record_and_counts_errors():
    adapter = RecordAdapterSink(ExplodingSink())
    await adapter.put(_stk_trade())              # out.put raises -> swallowed
    await adapter.put(_stk_trade())
    assert adapter.errors == 2


async def test_adapter_flush_delegates_to_out():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.flush()
    assert out.flushes == 1


# ------------------------------------------------------- provider selection

class _FactorySpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, provider, symbols, channels, out, registry, **kw):
        self.calls.append(dict(provider=provider, symbols=list(symbols),
                               channels=list(channels), out=out, registry=registry))
        return object()


@pytest.fixture
def factory(monkeypatch):
    spy = _FactorySpy()
    monkeypatch.setattr(live, "make_provider", spy)
    # Isolate from whatever keys exist on the host machine.
    for var in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "FINNHUB_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return spy


def test_providers_default_to_google_finance(factory):
    out = CaptureSink()
    providers = build_equity_providers(["AAPL", "SPY"], out, registry=object())
    assert len(providers) == 1
    call = factory.calls[0]
    assert call["provider"] == "google_finance"
    assert call["symbols"] == ["AAPL", "SPY"]    # no cap
    assert call["channels"] == ["trade"]
    assert call["out"] is out


def test_providers_pick_finnhub_and_cap_50(factory, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    syms = [f"S{i}" for i in range(60)]
    build_equity_providers(syms, CaptureSink(), registry=object())
    call = factory.calls[0]
    assert call["provider"] == "finnhub"
    assert call["symbols"] == syms[:50]          # trimmed, order preserved


def test_providers_pick_alpaca_and_cap_30(factory, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    monkeypatch.setenv("FINNHUB_API_KEY", "k")   # alpaca must win
    syms = [f"S{i}" for i in range(40)]
    build_equity_providers(syms, CaptureSink(), registry=object())
    call = factory.calls[0]
    assert call["provider"] == "alpaca"
    assert call["symbols"] == syms[:30]


def test_alpaca_needs_both_keys(factory, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")    # no secret
    build_equity_providers(["AAPL"], CaptureSink(), registry=object())
    assert factory.calls[0]["provider"] == "google_finance"


# ------------------------------------------------------------ feed start-up

async def test_start_equity_feed_wires_adapter_and_returns_task(factory, monkeypatch):
    seen: dict = {}

    async def fake_collect(providers, sink, *, max_reconnects=-1):
        seen.update(providers=providers, sink=sink, max_reconnects=max_reconnects)

    monkeypatch.setattr(live, "collect", fake_collect)
    out = CaptureSink()
    task = await start_equity_feed(out, ["AAPL"], max_reconnects=3)
    assert isinstance(task, asyncio.Task)
    await task
    assert isinstance(seen["sink"], RecordAdapterSink)
    assert seen["sink"].out is out               # adapter wraps the given sink
    assert seen["max_reconnects"] == 3
    assert len(seen["providers"]) == 1
    # The factory saw the adapter as the provider output sink too.
    assert factory.calls[0]["out"] is seen["sink"]


# ------------------------------------------------------------------ universe

def test_live_universe_shape():
    assert 20 <= len(LIVE_UNIVERSE) <= 40
    assert len(set(LIVE_UNIVERSE)) == len(LIVE_UNIVERSE)
    assert set(LIVE_UNIVERSE) <= set(UNIVERSE)   # drawn from existing tuples
    for anchor in ("SPY", "AAPL", "NVDA", "JPM"):
        assert anchor in LIVE_UNIVERSE
