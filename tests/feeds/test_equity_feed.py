# tests/feeds/test_equity_feed.py
import asyncio
import contextlib

import pytest
from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade

from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EXCHANGE, EquitySimFeed


@pytest.mark.asyncio
async def test_feed_emits_trades_into_sink():
    sink = QueueSink(maxsize=10_000)
    feed = EquitySimFeed(sink, seed=5, ticks_per_sec=2000, batch_dt=0.01)
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    recs = [sink.q.get_nowait() for _ in range(sink.q.qsize())]
    assert recs, "expected some trades"
    r = recs[0]
    assert isinstance(r, Trade)
    assert r.exchange == EXCHANGE
    assert r.side in (Side.BUY, Side.SELL)
    assert r.price > 0 and r.local_ts == r.exchange_ts
