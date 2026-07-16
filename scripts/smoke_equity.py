# scripts/smoke_equity.py
"""REAL-network smoke: Yahoo warmup bars + google_finance live equity feed.

Not collected by pytest (scripts/ sits outside testpaths). Run manually:
    python scripts/smoke_equity.py
Exits non-zero if warmup bars come back empty, or if the live feed produced
zero ticks while the US market is open per stockodile's USMarketCalendar.
"""
import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

FEED_WINDOW_S = 15.0


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=UTC).isoformat()


async def main() -> int:
    # Force the keyless google_finance provider path regardless of local env.
    for key in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "FINNHUB_API_KEY"):
        os.environ.pop(key, None)

    from entropy.feeds.bus import QueueSink
    from entropy.feeds.equities.live import start_equity_feed
    from entropy.feeds.warmup import warmup_equity_bars

    ok = True
    try:
        bars = await warmup_equity_bars("AAPL", "15m")
    except Exception as exc:
        print(f"warmup_equity_bars FAILED: {exc}")
        bars = []
    print(f"AAPL 15m warmup bars: {len(bars)}")
    if bars:
        print(f"  first: {_iso(bars[0].ts_ns)}  close={bars[0].close}")
        print(f"  last:  {_iso(bars[-1].ts_ns)}  close={bars[-1].close}")
    else:
        ok = False

    sink = QueueSink(maxsize=10_000)
    task, plan = await start_equity_feed(sink, ["AAPL", "MSFT", "NVDA", "SPY"])
    print(f"live feed provider: {plan.provider_name}")
    ticks = 0
    deadline = time.monotonic() + FEED_WINDOW_S
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            await asyncio.wait_for(sink.q.get(), timeout=remaining)
        except TimeoutError:
            break
        ticks += 1
    task.cancel()
    adapter = getattr(plan.providers[0], "out", None)  # RecordAdapterSink
    print(f"ticks in {FEED_WINDOW_S:.0f}s: {ticks}  "
          f"adapter errors: {getattr(adapter, 'errors', 'n/a')}")

    from stockodile.scheduler.calendar import USMarketCalendar
    market_open = bool(
        USMarketCalendar().is_market_open(datetime.now(ZoneInfo("America/New_York")))
    )
    print(f"US market open: {market_open}")
    if ticks == 0 and market_open:
        print("FAIL: market is open but the live feed produced no ticks")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
