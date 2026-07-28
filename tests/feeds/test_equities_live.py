"""Tests for the crocodile-backed live equity feed (no network)."""
import asyncio

import pytest
from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import OHLCV, Quote, Trade
from crocodile.core.sink.base import Sink

from entropy.feeds.equities import live
from entropy.feeds.equities.live import (
    EquityProviderPlan,
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


def _stk_trade(**kw) -> Trade:
    # Equity prints carry no aggressor side; providers emit UNKNOWN and the
    # adapter is what replaces it with a tick-rule side.
    base = dict(source="google_finance", symbol="aapl", symbol_raw="aapl",
                local_ts=2_000, asset_class=AssetClass.EQUITY, source_ts=1_000,
                id="t1", price=213.5, amount=100.0, side=Side.UNKNOWN)
    base.update(kw)
    return Trade(**base)


def _stk_bar(close: float, **kw) -> OHLCV:
    base = dict(source="stooq", symbol="spy", symbol_raw="spy", local_ts=5_000,
                asset_class=AssetClass.EQUITY, source_ts=4_000,
                interval="1d", open=1.0, high=2.0, low=0.5, close=close,
                volume=42.0)
    base.update(kw)
    return OHLCV(**base)


# ---------------------------------------------------------------- adapter map

async def test_adapter_side_stamps_equity_trade():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_trade())
    assert adapter.errors == 0
    assert len(out.records) == 1
    tr = out.records[0]
    assert isinstance(tr, Trade)
    assert tr.source == "google_finance"         # no "stk-" prefix any more
    assert tr.asset_class is AssetClass.EQUITY   # the prefix's job, done properly
    assert tr.symbol == "AAPL"                   # bare upper ticker
    assert tr.symbol_raw == "aapl"
    assert tr.source_ts == 1_000
    assert tr.local_ts == 2_000
    assert tr.id == "t1"
    assert tr.price == 213.5
    assert tr.amount == 100.0
    assert tr.side is Side.BUY                   # documented first-tick default


async def test_adapter_preserves_unstamped_source_ts():
    """A source that stamped nothing keeps reading as nothing.

    The pre-merge adapter substituted local_ts here, because the crypto Trade it
    built had no way to say "unstamped". The merged record does, and only
    local_ts is ever read downstream, so the substitution would now destroy the
    distinction while buying nothing.
    """
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_trade(source_ts=None))
    assert out.records[0].source_ts is None
    assert out.records[0].local_ts == 2_000


async def test_adapter_preserves_provenance_on_passthrough():
    """Side-stamping must not strip the fields the merge added."""
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_trade(prov_basis="alpaca_l1", prov_confidence=1.0,
                                 venue="XNAS"))
    tr = out.records[0]
    assert tr.prov_basis == "alpaca_l1"
    assert tr.prov_confidence == 1.0
    assert tr.venue == "XNAS"


async def test_adapter_maps_bar_to_trade():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_bar(close=1.5))
    tr = out.records[0]
    assert isinstance(tr, Trade)
    assert tr.source == "stooq"
    assert tr.asset_class is AssetClass.EQUITY
    assert tr.symbol == "SPY"
    assert tr.price == 1.5                       # close
    assert tr.amount == 42.0                     # volume
    assert tr.source_ts == 4_000
    assert tr.id == ""


async def test_adapter_bar_zero_volume_maps_to_zero_amount():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_bar(close=1.0, volume=0.0))
    assert out.records[0].amount == 0.0


async def test_adapter_ignores_other_record_types_silently():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    quote = Quote(source="finnhub", symbol="AAPL", symbol_raw="AAPL", local_ts=1,
                  asset_class=AssetClass.EQUITY, source_ts=None,
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


# ------------------------------------------------- tick-rule side inference

async def _sides_for(adapter: RecordAdapterSink, out: CaptureSink, prices) -> list[Side]:
    for i, px in enumerate(prices):
        await adapter.put(_stk_trade(price=px, id=f"t{i}"))
    return [tr.side for tr in out.records]


async def test_side_first_tick_defaults_to_buy():
    out = CaptureSink()
    assert await _sides_for(RecordAdapterSink(out), out, [100.0]) == [Side.BUY]


async def test_side_rising_prices_infer_buy():
    out = CaptureSink()
    sides = await _sides_for(RecordAdapterSink(out), out, [100.0, 100.5, 101.0])
    assert sides == [Side.BUY, Side.BUY, Side.BUY]


async def test_side_falling_prices_infer_sell():
    out = CaptureSink()
    sides = await _sides_for(RecordAdapterSink(out), out, [100.0, 99.5, 99.0])
    assert sides == [Side.BUY, Side.SELL, Side.SELL]


async def test_side_flat_price_carries_previous_side():
    out = CaptureSink()
    sides = await _sides_for(RecordAdapterSink(out), out, [100.0, 99.0, 99.0, 99.5, 99.5])
    assert sides == [Side.BUY, Side.SELL, Side.SELL, Side.BUY, Side.BUY]


async def test_side_state_is_per_symbol():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_trade(symbol="AAPL", price=100.0))
    await adapter.put(_stk_trade(symbol="MSFT", price=500.0))
    await adapter.put(_stk_trade(symbol="AAPL", price=99.0))   # AAPL down-tick
    await adapter.put(_stk_trade(symbol="MSFT", price=501.0))  # MSFT up-tick
    assert [t.side for t in out.records] == [Side.BUY, Side.BUY, Side.SELL, Side.BUY]


async def test_side_applies_to_bar_closes():
    out = CaptureSink()
    adapter = RecordAdapterSink(out)
    await adapter.put(_stk_bar(close=100.0))
    await adapter.put(_stk_bar(close=99.0))
    await adapter.put(_stk_bar(close=99.8))
    assert [t.side for t in out.records] == [Side.BUY, Side.SELL, Side.BUY]


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
    plan = build_equity_providers(["AAPL", "SPY"], out, registry=object())
    assert isinstance(plan, EquityProviderPlan)
    assert plan.provider_name == "google_finance"
    assert plan.trimmed_symbols == []            # no cap
    assert len(plan.providers) == 1
    call = factory.calls[0]
    assert call["provider"] == "google_finance"
    assert call["symbols"] == ["AAPL", "SPY"]
    assert call["channels"] == ["trade"]
    assert call["out"] is out


def test_providers_pick_finnhub_and_cap_50(factory, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    syms = [f"S{i}" for i in range(60)]
    plan = build_equity_providers(syms, CaptureSink(), registry=object())
    assert plan.provider_name == "finnhub"
    assert factory.calls[0]["symbols"] == syms[:50]  # trimmed, order preserved
    assert plan.trimmed_symbols == syms[50:]         # dropped tail is reported


def test_providers_pick_alpaca_and_cap_30(factory, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    monkeypatch.setenv("FINNHUB_API_KEY", "k")   # alpaca must win
    syms = [f"S{i}" for i in range(40)]
    plan = build_equity_providers(syms, CaptureSink(), registry=object())
    assert plan.provider_name == "alpaca"
    assert factory.calls[0]["symbols"] == syms[:30]
    assert plan.trimmed_symbols == syms[30:]


def test_alpaca_needs_both_keys(factory, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")    # no secret
    plan = build_equity_providers(["AAPL"], CaptureSink(), registry=object())
    assert plan.provider_name == "google_finance"


# ------------------------------------------------------------ feed start-up

async def test_start_equity_feed_wires_adapter_and_returns_task(factory, monkeypatch):
    seen: dict = {}

    async def fake_collect(providers, sink, *, max_reconnects=-1):
        seen.update(providers=providers, sink=sink, max_reconnects=max_reconnects)

    monkeypatch.setattr(live, "collect", fake_collect)
    out = CaptureSink()
    task, plan = await start_equity_feed(out, ["AAPL"], max_reconnects=3)
    assert isinstance(task, asyncio.Task)
    await task
    assert isinstance(seen["sink"], RecordAdapterSink)
    assert seen["sink"].out is out               # adapter wraps the given sink
    assert seen["max_reconnects"] == 3
    assert seen["providers"] is plan.providers
    assert plan.provider_name == "google_finance"
    assert plan.trimmed_symbols == []
    # The factory saw the adapter as the provider output sink too.
    assert factory.calls[0]["out"] is seen["sink"]


# ------------------------------------------------------------------ universe

def test_live_universe_shape():
    assert 20 <= len(LIVE_UNIVERSE) <= 40
    assert len(set(LIVE_UNIVERSE)) == len(LIVE_UNIVERSE)
    assert set(LIVE_UNIVERSE) <= set(UNIVERSE)   # drawn from existing tuples
    for anchor in ("SPY", "AAPL", "NVDA", "JPM"):
        assert anchor in LIVE_UNIVERSE
