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

import msgspec
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

    Sides are inferred with the tick rule (stockodile trades carry no side, and
    BreadthTracker counts every non-"sell" side as a buy — emitting UNKNOWN
    would skew breadth to 100% buy).
    """

    def __init__(self, out: Sink) -> None:
        self.out = out
        self.errors = 0
        self._last: dict[str, tuple[float, Side]] = {}  # symbol -> (price, side)

    def _infer_side(self, symbol: str, price: float) -> Side:
        """Tick rule: up-tick BUY, down-tick SELL, flat carries the previous side.

        The first print of a symbol has nothing to compare against and defaults
        to BUY (documented choice; any fixed default is equally arbitrary).
        """
        prev = self._last.get(symbol)
        if prev is None or price > prev[0]:
            side = Side.BUY
        elif price < prev[0]:
            side = Side.SELL
        else:
            side = prev[1]
        self._last[symbol] = (price, side)
        return side

    async def put(self, record: Any) -> None:
        try:
            if isinstance(record, StkTrade):
                price, amount, rec_id = record.price, record.size, record.id
            elif isinstance(record, StkBar):
                # Forward-looking: providers are currently built with
                # channels=["trade"] and emit no Bars; kept so bar-emitting
                # providers plug in unchanged. CAVEAT: if a bar channel is ever
                # wired, the WHOLE bar's volume lands on one tick-rule-sided
                # Trade, skewing breadth toward that side — it needs a per-bar
                # buy/sell split before going live.
                price, amount, rec_id = record.close, record.volume or 0.0, ""
            else:
                return  # quotes/fundamentals/etc.: not part of the tick pipeline
            symbol = record.symbol.upper()
            trade = Trade(
                exchange=f"stk-{record.provider}",
                symbol=symbol,
                symbol_raw=record.symbol_raw,
                exchange_ts=record.source_ts if record.source_ts is not None
                else record.local_ts,
                local_ts=record.local_ts,
                id=rec_id,
                price=price,
                amount=amount,
                side=self._infer_side(symbol, price),
            )
            await self.out.put(trade)
        except Exception:
            self.errors += 1
            log.debug("equity adapter dropped record", exc_info=True)

    async def flush(self) -> None:
        await self.out.flush()


class EquityProviderPlan(msgspec.Struct, frozen=True):
    """Provider selection result, structural so the app can surface trim info
    in its console widget instead of relying on stderr logging."""

    providers: list[Any]
    provider_name: str
    trimmed_symbols: list[str]  # symbols dropped by the provider's hard cap


def build_equity_providers(
    symbols: Sequence[str],
    out: Sink,
    registry: InstrumentRegistry,
) -> EquityProviderPlan:
    """Pick ONE stockodile provider from the environment and build it.

    Alpaca (both keys) > Finnhub (key) > Google Finance (keyless default).
    Symbol lists are trimmed to the provider's hard cap, preserving order;
    dropped symbols are reported in the returned plan.
    """
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET"):
        name, cap = "alpaca", _ALPACA_CAP
    elif os.environ.get("FINNHUB_API_KEY"):
        name, cap = "finnhub", _FINNHUB_CAP
    else:
        name, cap = "google_finance", None
    syms = list(symbols)
    trimmed: list[str] = []
    if cap is not None and len(syms) > cap:
        trimmed, syms = syms[cap:], syms[:cap]
        # debug, not warning: no logging handler is configured, so stderr
        # writes would corrupt the Textual alternate screen. The TUI surfaces
        # `trimmed_symbols` from the plan instead.
        log.debug("%s live feed capped at %d symbols; dropped %d: %s",
                  name, cap, len(trimmed), trimmed)
    provider = make_provider(name, syms, ["trade"], out=out, registry=registry)
    return EquityProviderPlan(providers=[provider], provider_name=name,
                              trimmed_symbols=trimmed)


async def start_equity_feed(
    sink: Sink,
    symbols: Sequence[str],
    *,
    max_reconnects: int = -1,
) -> tuple[asyncio.Task[None], EquityProviderPlan]:
    """Start the live equity feed; mirrors crypto.start_feed's calling shape,
    plus the provider plan so the app can report what is actually running.

    Unlike crypcodile connectors, stockodile providers assign their own
    transport in __init__ (or override run() entirely), so no manual
    AiohttpWsTransport wiring is needed here.
    """
    adapter = RecordAdapterSink(sink)
    # Intentionally unpopulated: equities have no discover step (unlike crypto's
    # discover_universe); Google falls back to bare-ticker resolution.
    registry = InstrumentRegistry()
    plan = build_equity_providers(symbols, adapter, registry)
    task = asyncio.create_task(collect(plan.providers, adapter,
                                       max_reconnects=max_reconnects))
    return task, plan
