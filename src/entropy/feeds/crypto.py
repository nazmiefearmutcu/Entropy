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

async def discover_universe(
    registry: InstrumentRegistry,
    cb_whitelist: Sequence[str] = COINBASE_MAJORS,
    bn_whitelist: Sequence[str] = BINANCE_MAJORS,
) -> tuple[list[str], list[str]]:
    dummy = QueueSink()
    cb = CoinbaseConnector(symbols=[], channels=[], out=dummy, registry=registry)
    bn = BinanceConnector(symbols=[], channels=[], out=dummy, registry=registry, market="spot")
    cb_insts: list[Instrument] = await cb.list_instruments()
    bn_insts: list[Instrument] = await bn.list_instruments()
    cb_ok = {i.symbol_raw for i in cb_insts if i.kind == Kind.SPOT and i.quote == "USD"}
    bn_ok = {i.symbol_raw for i in bn_insts if i.kind == Kind.SPOT and i.quote == "USDT"}
    for i in cb_insts + bn_insts:
        registry.add(i)
    cb_syms = [s for s in cb_whitelist if s in cb_ok]
    bn_syms = [s for s in bn_whitelist if s in bn_ok]
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
