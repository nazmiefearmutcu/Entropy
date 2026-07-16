# src/entropy/feeds/warmup.py
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from entropy.strategy.engine import Bar

_YAHOO_TIMEOUT_S = 10.0


def bars_from_ohlcv(ohlcv_rows: Iterable[Any]) -> list[Bar]:
    """Map any OHLCV-like rows (Crypcodile OHLCV) to strategy/chart Bars."""
    out: list[Bar] = []
    for o in ohlcv_rows:
        if getattr(o, "close", None) is None:
            continue
        ts = o.exchange_ts if getattr(o, "exchange_ts", None) is not None else o.local_ts
        out.append(Bar(ts_ns=int(ts), close=float(o.close),
                       high=float(o.high), low=float(o.low)))
    return out

async def warmup_klines(symbol_raw: str, interval: str = "1m", limit: int = 200) -> list[Bar]:
    """Fetch recent Binance klines as warmup Bars. Network call."""
    import time

    from crypcodile.exchanges.binance.backfill import make_live_backfill
    bf = make_live_backfill()
    now = time.clock_gettime_ns(time.CLOCK_REALTIME)
    start = now - limit * 60 * 1_000_000_000
    rows = []
    async for bar in bf.backfill_klines(venue="binance-spot", symbol=symbol_raw,
                                        interval=interval, start_ns=start, end_ns=now):
        rows.append(bar)
    return bars_from_ohlcv(rows)


def bars_from_stockodile(rows: Iterable[Any]) -> list[Bar]:
    """Map stockodile Bar records to strategy/chart Bars.

    Sibling of bars_from_ohlcv: stockodile bars carry the bar-start timestamp
    in ``source_ts`` (nullable) instead of ``exchange_ts``, with ``local_ts``
    (ingest time) as the fallback — same preference order, different attr.
    """
    out: list[Bar] = []
    for r in rows:
        if getattr(r, "close", None) is None:
            continue
        ts = r.source_ts if getattr(r, "source_ts", None) is not None else r.local_ts
        out.append(Bar(ts_ns=int(ts), close=float(r.close),
                       high=float(r.high), low=float(r.low)))
    return out


async def _fetch_yahoo_bars(symbol: str, interval: str) -> list[Any]:
    """Fetch raw stockodile Bars via YahooClient. Module-level indirection so
    tests can monkeypatch it; stockodile is imported LAZILY (the sim path must
    never touch it)."""
    from stockodile.providers.yahoo.client import YahooClient
    rows: list[Any] = await YahooClient().fetch_intraday_bars(symbol, interval)
    return rows


async def warmup_equity_bars(symbol: str, interval: str = "15m",
                             limit: int = 64) -> list[Bar]:
    """Fetch recent Yahoo intraday bars as warmup Bars. Network call.

    Returns the newest ``limit`` bars sorted ascending by timestamp, keeping
    only rows matching ``interval``. Raises on failure (timeout, network,
    empty result) — the caller decides the fallback.
    """
    rows = await asyncio.wait_for(_fetch_yahoo_bars(symbol, interval),
                                  timeout=_YAHOO_TIMEOUT_S)
    wanted = [r for r in rows if getattr(r, "interval", interval) == interval]
    bars = sorted(bars_from_stockodile(wanted), key=lambda b: b.ts_ns)
    if not bars:
        raise RuntimeError(f"yahoo returned no {interval} bars for {symbol}")
    return bars[-limit:]
