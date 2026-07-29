from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from crocodile.core.connector import Connector
from crocodile.core.ingest.transport import AiohttpWsTransport
from crocodile.core.sink.base import Sink
from crocodile.crypto.client.collect import collect
from crocodile.crypto.exchanges.binance.connector import BinanceConnector
from crocodile.crypto.exchanges.coinbase.connector import CoinbaseConnector
from crocodile.crypto.exchanges.factory import make_connector
from crocodile.crypto.instruments.registry import Instrument, InstrumentRegistry, Kind
from crocodile.crypto.instruments.universe import top_symbols_by_volume

from .bus import QueueSink

log = logging.getLogger(__name__)

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
    """Build a live connector for ANY venue crocodile can reach.

    ``make_connector`` resolves the ten hand-written connectors by name and
    falls through to the universal ccxt connector for the other ~100 venues, so
    this works for the whole set — but the two kinds differ on who owns the
    socket. A hand-written connector publishes a ``ws_url`` and expects the
    caller to attach the transport (it is deliberately never auto-set). The ccxt
    connector drives ccxt.pro's own socket, overrides ``run()``, and reports an
    empty ``ws_url``; attaching a transport for "" would build a client for an
    address that does not exist. So attach one only when there is a URL to
    attach it to.
    """
    c = make_connector(exchange, list(symbols), list(channels), out=sink, registry=registry, **kw)
    if getattr(c, "ws_url", ""):
        c.transport = AiohttpWsTransport(c.ws_url)
    return c


async def venue_symbols(
    exchange: str,
    n: int = 30,
    *,
    quote: str | None = "USDT",
    kinds: set[Kind] | None = None,
) -> list[str]:
    """The ``n`` most-liquid symbols on *exchange*, ranked by 24h quote volume.

    This is the general form of the curated ``*_MAJORS`` whitelists below: those
    name fourteen coins picked by hand on two venues, this asks any of
    crocodile's ~109 venues what its own liquid core is. Ranking needs live
    ticker volume, so it is a network call and best-effort — an unreachable
    venue yields an empty list rather than raising, matching how
    ``_resolve_symbols`` already treats failed discovery.
    """
    try:
        return await top_symbols_by_volume(exchange, n, quote=quote, kinds=kinds)
    except Exception:
        log.debug("venue_symbols(%s) failed; caller gets an empty list",
                  exchange, exc_info=True)
        return []

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
    *,
    venues: Mapping[str, int] | None = None,
) -> asyncio.Task[None]:
    """Start the live crypto feed.

    The default is unchanged: the curated majors on Coinbase and Binance, which
    is what a cold start should cost. ``venues`` adds any other venue crocodile
    can reach — ``{"kraken": 20, "bybit": 30}`` streams each one's 20/30
    most-liquid symbols alongside the defaults. Venue names are the same ones
    ``crocodile.crypto.exchanges.factory.list_all_exchanges()`` reports: the ten
    hand-written connectors plus every ccxt venue.

    A venue that cannot be reached or that ranks to nothing is skipped rather
    than failing the whole feed — one unreachable exchange must not cost the
    others their stream.
    """
    registry = InstrumentRegistry()
    cb_syms, bn_syms = await discover_universe(registry)
    connectors: list[Connector] = []
    if cb_syms:
        connectors.append(build_live("coinbase", cb_syms, channels, sink, registry))
    if bn_syms:
        connectors.append(build_live("binance", bn_syms, channels, sink, registry, market="spot"))
    for venue, n in (venues or {}).items():
        syms = await venue_symbols(venue, n)
        if not syms:
            log.debug("venue %s contributed no symbols; skipped", venue)
            continue
        connectors.append(build_live(venue, syms, channels, sink, registry))
    return asyncio.create_task(collect(connectors, sink, max_reconnects=-1))
