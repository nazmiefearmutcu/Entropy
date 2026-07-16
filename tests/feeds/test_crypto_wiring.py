import pytest

from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import _resolve_symbols, build_live


def test_build_live_sets_transport():
    from crypcodile.instruments.registry import InstrumentRegistry
    sink = QueueSink()
    reg = InstrumentRegistry()
    c = build_live("coinbase", ["BTC-USD"], ["trade"], sink, reg)
    assert c.transport is not None          # the load-bearing fix
    assert c.ws_url.startswith("wss://")


@pytest.mark.asyncio
async def test_resolve_symbols_falls_back_when_discovery_fails():
    """A stale/unreachable list_instruments() must degrade to the whitelist,
    not crash the feed (the live WS feed does not depend on discovery)."""
    from crypcodile.instruments.registry import InstrumentRegistry

    class _Broken:
        async def list_instruments(self):
            raise RuntimeError("404 Not Found")

    reg = InstrumentRegistry()
    out = await _resolve_symbols(_Broken(), reg, ["BTC-USD", "ETH-USD"], "USD")
    assert out == ["BTC-USD", "ETH-USD"]


def test_binance_trade_canonical_matches_app_default_even_with_populated_registry():
    """Prove the app default crypto_strategy_symbol ("binance-spot:BTCUSDT") is the
    canonical a live Binance spot trade carries — INCLUDING after discover_universe
    populates the registry.

    BinanceConnector.list_instruments() registers Instruments with
    canonical=f"binance:{sym}" / exchange="binance", which LOOKS like it would
    diverge from the app default. But the normalizer keys its registry lookup by
    the connector's venue tag ("binance-spot"), and InstrumentRegistry keys by
    inst.exchange ("binance") — so the lookup always misses and Trade.symbol falls
    back to f"{venue}:{raw}" == "binance-spot:BTCUSDT", exactly the app default.
    If crypcodile ever aligns those keys, Trade.symbol flips to "binance:BTCUSDT"
    and this test fails — the signal to resolve the configured symbol through the
    registry instead of relying on the fallback.
    """
    from crypcodile.exchanges.binance.connector import BinanceConnector
    from crypcodile.exchanges.binance.normalize import normalize_message
    from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
    from crypcodile.schema.records import Trade

    from entropy.app import AppConfig

    registry = InstrumentRegistry()
    # Exactly the Instrument list_instruments() builds for BTCUSDT and
    # _resolve_symbols() then add()s (see crypcodile/exchanges/binance/connector.py:
    # canonical=f"{EXCHANGE}:{sym}" with EXCHANGE = "binance").
    registry.add(Instrument(
        canonical="binance:BTCUSDT", exchange="binance", symbol_raw="BTCUSDT",
        kind=Kind.SPOT, base="BTC", quote="USDT", tick_size=0.01,
    ))
    bn = BinanceConnector(symbols=["BTCUSDT"], channels=["trade"], out=QueueSink(),
                          registry=registry, market="spot")

    # The normalizer's registry-hit path never fires: venue tag != inst.exchange.
    assert bn._venue == "binance-spot"
    assert registry.get_raw(bn._venue, "BTCUSDT") is None
    assert registry.by_raw("binance", "BTCUSDT").canonical == "binance:BTCUSDT"

    msg = {
        "stream": "btcusdt@aggTrade",
        "data": {"s": "BTCUSDT", "m": False, "T": 1_700_000_000_000,
                 "E": 1_700_000_000_000, "a": 1, "p": "50000.0", "q": "0.5"},
    }
    (trade,) = list(normalize_message(msg, local_ts=1, venue=bn._venue,
                                      registry=registry))
    assert isinstance(trade, Trade)
    assert trade.symbol == "binance-spot:BTCUSDT"
    assert trade.symbol == AppConfig().crypto_strategy_symbol
