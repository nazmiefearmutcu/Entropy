# src/entropy/feeds/equities/live.py
"""Real US-equity feed: stockodile providers bridged into the crypcodile bus.

stockodile emits its own record types (``stockodile.schema.records``); the
Entropy pipeline speaks crypcodile ``Trade``. ``RecordAdapterSink`` translates
between the two so the Engine/UI see equities exactly like crypto ticks,
keyed by BARE upper tickers ("AAPL") with the exchange prefixed "stk-".
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from typing import Any

from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade
from crypcodile.sink.base import Sink
from stockodile.client.collect import collect
from stockodile.providers.factory import make_provider
from stockodile.reference.registry import InstrumentRegistry
from stockodile.schema.records import Bar as StkBar
from stockodile.schema.records import Trade as StkTrade

log = logging.getLogger(__name__)

# Provider hard limits (the connectors raise ValueError above these).
_ALPACA_CAP = 30   # Alpaca iex plan: max 30 trade/quote symbols
_FINNHUB_CAP = 50  # Finnhub free tier: max 50 WS subscriptions


class RecordAdapterSink(Sink):
    """crypcodile Sink that accepts stockodile records and forwards crypcodile Trades.

    Runs inside stockodile's collect() TaskGroup: a raised exception would kill
    the whole feed, so per-record failures are swallowed and counted instead.
    """

    def __init__(self, out: Sink) -> None:
        self.out = out
        self.errors = 0

    async def put(self, record: Any) -> None:
        try:
            if isinstance(record, StkTrade):
                trade = Trade(
                    exchange=f"stk-{record.provider}",
                    symbol=record.symbol.upper(),
                    symbol_raw=record.symbol_raw,
                    exchange_ts=record.source_ts if record.source_ts is not None
                    else record.local_ts,
                    local_ts=record.local_ts,
                    id=record.id,
                    price=record.price,
                    amount=record.size,
                    side=Side.UNKNOWN,  # stockodile Trade carries no side field
                )
            elif isinstance(record, StkBar):
                trade = Trade(
                    exchange=f"stk-{record.provider}",
                    symbol=record.symbol.upper(),
                    symbol_raw=record.symbol_raw,
                    exchange_ts=record.source_ts if record.source_ts is not None
                    else record.local_ts,
                    local_ts=record.local_ts,
                    id="",
                    price=record.close,
                    amount=record.volume or 0.0,
                    side=Side.UNKNOWN,
                )
            else:
                return  # quotes/fundamentals/etc.: not part of the tick pipeline
            await self.out.put(trade)
        except Exception:
            self.errors += 1
            log.debug("equity adapter dropped record", exc_info=True)

    async def flush(self) -> None:
        await self.out.flush()


def build_equity_providers(
    symbols: Sequence[str],
    out: Sink,
    registry: InstrumentRegistry,
) -> list[Any]:
    """Pick ONE stockodile provider from the environment and build it.

    Alpaca (both keys) > Finnhub (key) > Google Finance (keyless default).
    Symbol lists are trimmed to the provider's hard cap, preserving order.
    """
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET"):
        name, cap = "alpaca", _ALPACA_CAP
    elif os.environ.get("FINNHUB_API_KEY"):
        name, cap = "finnhub", _FINNHUB_CAP
    else:
        name, cap = "google_finance", None
    syms = list(symbols)
    if cap is not None and len(syms) > cap:
        log.warning("%s live feed capped at %d symbols; trimming %d -> %d",
                    name, cap, len(syms), cap)
        syms = syms[:cap]
    return [make_provider(name, syms, ["trade"], out=out, registry=registry)]


async def start_equity_feed(
    sink: Sink,
    symbols: Sequence[str],
    *,
    max_reconnects: int = -1,
) -> asyncio.Task[None]:
    """Start the live equity feed; mirrors crypto.start_feed's calling shape.

    Unlike crypcodile connectors, stockodile providers assign their own
    transport in __init__ (or override run() entirely), so no manual
    AiohttpWsTransport wiring is needed here.
    """
    adapter = RecordAdapterSink(sink)
    registry = InstrumentRegistry()
    providers = build_equity_providers(symbols, adapter, registry)
    return asyncio.create_task(collect(providers, adapter, max_reconnects=max_reconnects))
