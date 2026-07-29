import pytest

from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import _resolve_symbols, build_live


def test_build_live_sets_transport():
    from crocodile.crypto.instruments.registry import InstrumentRegistry
    sink = QueueSink()
    reg = InstrumentRegistry()
    c = build_live("coinbase", ["BTC-USD"], ["trade"], sink, reg)
    assert c.transport is not None          # the load-bearing fix
    assert c.ws_url.startswith("wss://")


@pytest.mark.asyncio
async def test_resolve_symbols_falls_back_when_discovery_fails():
    """A stale/unreachable list_instruments() must degrade to the whitelist,
    not crash the feed (the live WS feed does not depend on discovery)."""
    from crocodile.crypto.instruments.registry import InstrumentRegistry

    class _Broken:
        async def list_instruments(self):
            raise RuntimeError("404 Not Found")

    reg = InstrumentRegistry()
    out = await _resolve_symbols(_Broken(), reg, ["BTC-USD", "ETH-USD"], "USD")
    assert out == ["BTC-USD", "ETH-USD"]


@pytest.mark.asyncio
async def test_binance_trade_canonical_matches_app_default_even_with_populated_registry():
    """Prove the app default crypto_strategy_symbol ("binance-spot:BTCUSDT") is the
    canonical a live Binance spot trade carries — INCLUDING after discover_universe
    populates the registry.

    BinanceConnector.list_instruments() registers Instruments keyed by the
    connector's venue tag: canonical=f"binance-spot:{sym}" / exchange="binance-spot".
    The normalizer looks up registry.get_raw(venue, raw) with the same venue tag,
    so with a populated registry the lookup HITS and Trade.symbol carries the
    registry canonical — which equals the f"{venue}:{raw}" fallback by design.
    (Historically the connector registered under bare "binance", every lookup
    missed, and only the fallback kept this invariant alive.)

    This test drives crocodile's real discovery → registration → lookup pipeline
    (list_instruments with a canned REST payload, exactly what _resolve_symbols
    does at startup). If crocodile ever changes the registration keying or the
    canonical format, this fails loudly — the signal to update
    AppConfig.crypto_strategy_symbol in the same change.
    """
    from unittest.mock import patch

    from crocodile.core.schema.records import Trade
    from crocodile.crypto.exchanges.binance.connector import BinanceConnector
    from crocodile.crypto.exchanges.binance.normalize import normalize_message
    from crocodile.crypto.instruments.registry import InstrumentRegistry

    from entropy.app import AppConfig

    registry = InstrumentRegistry()
    bn = BinanceConnector(symbols=["BTCUSDT"], channels=["trade"], out=QueueSink(),
                          registry=registry, market="spot")

    exchange_info = {
        "symbols": [{
            "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"}],
        }]
    }

    async def fake_http_get(url, params=None, **kwargs):
        return exchange_info

    # Same registration path _resolve_symbols() runs at startup.
    with patch.object(bn, "http_get", new=fake_http_get):
        for inst in await bn.list_instruments():
            registry.add(inst)

    # The normalizer's registry lookup now HITS with the venue tag.
    assert bn._venue == "binance-spot"
    hit = registry.get_raw(bn._venue, "BTCUSDT")
    assert hit is not None
    assert hit.canonical == "binance-spot:BTCUSDT"

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


# --- the ~100 ccxt venues: who owns the socket -------------------------------

def test_build_live_attaches_a_transport_only_when_there_is_a_url():
    """make_connector falls through to the universal ccxt connector for any venue
    without a hand-written one. Those drive ccxt.pro's own socket and report an
    empty ws_url, so handing them AiohttpWsTransport("") would build a client for
    an address that does not exist."""
    from crocodile.crypto.instruments.registry import InstrumentRegistry

    from entropy.feeds.bus import QueueSink
    from entropy.feeds.crypto import build_live

    registry = InstrumentRegistry()

    native = build_live("binance", ["BTCUSDT"], ["trade"], QueueSink(), registry,
                        market="spot")
    assert native.ws_url, "hand-written connector should publish a ws_url"
    assert native.transport is not None, "and the caller must attach the socket"

    ccxt_venue = build_live("kraken", ["BTC/USDT"], ["trade"], QueueSink(), registry)
    assert ccxt_venue.ws_url == "", "ccxt connector owns its socket"
    assert ccxt_venue.transport is None, "so no transport should be forced onto it"


async def test_venue_symbols_swallows_an_unreachable_venue():
    """One unreachable exchange must not cost the others their stream."""
    from entropy.feeds.crypto import venue_symbols

    assert await venue_symbols("not-a-real-exchange-xyz", 5) == []
