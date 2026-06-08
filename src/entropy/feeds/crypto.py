from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from crypcodile.client.collect import collect
from crypcodile.exchanges.base import Connector
from crypcodile.exchanges.binance.connector import BinanceConnector
from crypcodile.exchanges.coinbase.connector import CoinbaseConnector
from crypcodile.exchanges.factory import make_connector
from crypcodile.ingest.transport import AiohttpWsTransport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.sink.base import Sink

from .bus import QueueSink

# Curated liquid majors (intersected with discovered instruments at startup).
COINBASE_MAJORS = ("BTC-USD","ETH-USD","SOL-USD","XRP-USD","DOGE-USD","ADA-USD","AVAX-USD",
                   "LINK-USD","LTC-USD","BCH-USD","DOT-USD","UNI-USD","AAVE-USD","XLM-USD")
BINANCE_MAJORS  = ("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT",
                   "LINKUSDT","LTCUSDT","BCHUSDT","DOTUSDT","UNIUSDT","AAVEUSDT","XLMUSDT")

def build_live(
    exchange: str,
    symbols: Sequence[str],
    channels: Sequence[str],
    sink: Sink,
    registry: InstrumentRegistry,
    **kw: Any,
) -> Connector:
    c = make_connector(exchange, list(symbols), list(channels), out=sink, registry=registry, **kw)
    c.transport = AiohttpWsTransport(c.ws_url)   # REQUIRED — never auto-set
    return c

async def _resolve_symbols(
    connector: Connector,
    registry: InstrumentRegistry,
    whitelist: Sequence[str],
    quote: str,
) -> list[str]:
    """Intersect the curated whitelist with the exchange's live instrument list.

    Discovery is best-effort: if list_instruments() fails (stale REST endpoint,
    404, or no connectivity) we fall back to the raw whitelist. The live WS feed
    does not depend on discovery — it subscribes to the symbols directly — and
    the normalizer's fallback canonical (e.g. "coinbase:BTC-USD") is exactly the
    key the TUI uses, so the feed still works without a populated registry.
    """
    try:
        insts: list[Instrument] = await connector.list_instruments()
    except Exception:
        return list(whitelist)
    ok = {i.symbol_raw for i in insts if i.kind == Kind.SPOT and i.quote == quote}
    for i in insts:
        registry.add(i)
    return [s for s in whitelist if s in ok]

async def discover_universe(
    registry: InstrumentRegistry,
    cb_whitelist: Sequence[str] = COINBASE_MAJORS,
    bn_whitelist: Sequence[str] = BINANCE_MAJORS,
) -> tuple[list[str], list[str]]:
    dummy = QueueSink()
    cb = CoinbaseConnector(symbols=[], channels=[], out=dummy, registry=registry)
    bn = BinanceConnector(symbols=[], channels=[], out=dummy, registry=registry, market="spot")
    cb_syms = await _resolve_symbols(cb, registry, cb_whitelist, "USD")
    bn_syms = await _resolve_symbols(bn, registry, bn_whitelist, "USDT")
    return cb_syms, bn_syms

async def start_feed(
    sink: QueueSink,
    channels: Sequence[str] = ("trade",),
) -> asyncio.Task[None]:
    registry = InstrumentRegistry()
    cb_syms, bn_syms = await discover_universe(registry)
    connectors: list[Connector] = []
    if cb_syms:
        connectors.append(build_live("coinbase", cb_syms, channels, sink, registry))
    if bn_syms:
        connectors.append(build_live("binance", bn_syms, channels, sink, registry, market="spot"))
    return asyncio.create_task(collect(connectors, sink, max_reconnects=-1))
