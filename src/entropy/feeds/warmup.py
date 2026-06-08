# src/entropy/feeds/warmup.py
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from entropy.strategy.engine import Bar


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
