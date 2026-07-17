# src/entropy/feeds/warmup.py
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from entropy.strategy.engine import Bar

# Must exceed YahooClient's internal 429 backoff ladder (5s -> 10s -> 20s):
# a 10s outer timeout guaranteed a spurious fallback after a single 429.
_YAHOO_TIMEOUT_S = 30.0

_NS_PER_S = 1_000_000_000
_4H_NS = 4 * 3_600 * _NS_PER_S

# Binance-style interval suffixes ("30m", "1d", "1w"...) for intervals that are
# not engine timeframes.
_INTERVAL_UNIT_NS = {
    "s": _NS_PER_S,
    "m": 60 * _NS_PER_S,
    "h": 3_600 * _NS_PER_S,
    "d": 86_400 * _NS_PER_S,
    "w": 7 * 86_400 * _NS_PER_S,
}


def _interval_ns(interval: str) -> int:
    """Bar span of a kline interval string ("1m"/"15m"/"1h"/"4h"/"1d"…).

    Engine timeframe names resolve via TIMEFRAMES; other Binance-style strings
    parse as <count><unit>. Unrecognized strings fall back to 1m (the legacy
    assumption) — warmup is best-effort, a bad name must not raise here.
    """
    from entropy.engine.timeframe import TIMEFRAMES
    tf = TIMEFRAMES.get(interval)
    if tf is not None:
        return tf.bar_ns
    unit_ns = _INTERVAL_UNIT_NS.get(interval[-1:].lower())
    if unit_ns is not None and interval[:-1].isdigit():
        return int(interval[:-1]) * unit_ns
    return 60 * _NS_PER_S


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
    # Window must span `limit` bars of the ACTUAL interval: a fixed 60s-per-bar
    # window served ~13 bars of history on 15m (EMA21 never seeded) and <=3 on 1h.
    start = now - limit * _interval_ns(interval)
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
    never touch it). The client owns a requests.Session — close it even when
    the fetch raises or the outer timeout cancels us."""
    from stockodile.providers.yahoo.client import YahooClient
    client = YahooClient()
    try:
        rows: list[Any] = await client.fetch_intraday_bars(symbol, interval)
    finally:
        await client.close()
    return rows


def _aggregate_4h(bars: list[Bar]) -> list[Bar]:
    """Fold ascending 1h Bars into 4h Bars, bucketed on 4h ts boundaries
    (h=max, l=min, c=last close; Bar carries no open/volume to aggregate)."""

    def _hi(a: float | None, b: float | None) -> float | None:
        return b if a is None else a if b is None else max(a, b)

    def _lo(a: float | None, b: float | None) -> float | None:
        return b if a is None else a if b is None else min(a, b)

    out: list[Bar] = []
    for b in bars:
        t0 = b.ts_ns // _4H_NS * _4H_NS
        if out and out[-1].ts_ns == t0:
            prev = out[-1]
            out[-1] = Bar(ts_ns=t0, close=b.close,
                          high=_hi(prev.high, b.high), low=_lo(prev.low, b.low))
        else:
            out.append(Bar(ts_ns=t0, close=b.close, high=b.high, low=b.low))
    return out


async def warmup_equity_bars(symbol: str, interval: str = "15m",
                             limit: int = 64) -> list[Bar]:
    """Fetch recent Yahoo intraday bars as warmup Bars. Network call.

    Returns the newest ``limit`` bars sorted ascending by timestamp, keeping
    only rows matching the fetched interval. Raises on failure (timeout,
    network, empty result) — the caller decides the fallback.

    yfinance has no "4h" interval (1m/2m/5m/15m/30m/60m/90m/1h/1d): the 4h
    timeframe fetches 1h bars instead and aggregates them into 4h buckets.
    """
    fetch_interval = "1h" if interval == "4h" else interval
    rows = await asyncio.wait_for(_fetch_yahoo_bars(symbol, fetch_interval),
                                  timeout=_YAHOO_TIMEOUT_S)
    wanted = [r for r in rows if getattr(r, "interval", fetch_interval) == fetch_interval]
    bars = sorted(bars_from_stockodile(wanted), key=lambda b: b.ts_ns)
    if interval == "4h":
        bars = _aggregate_4h(bars)
    if not bars:
        raise RuntimeError(f"yahoo returned no {interval} bars for {symbol}")
    return bars[-limit:]
