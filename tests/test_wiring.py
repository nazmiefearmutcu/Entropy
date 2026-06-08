# tests/test_wiring.py
import asyncio
import contextlib

import pytest

from entropy.engine.engine import Engine
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed


@pytest.mark.asyncio
async def test_sim_feed_drives_engine_snapshot():
    sink = QueueSink(maxsize=50_000)
    feed = EquitySimFeed(sink, seed=11, ticks_per_sec=3000, batch_dt=0.01)
    engine = Engine()
    ft = asyncio.create_task(feed.run())
    await asyncio.sleep(0.1)
    # drain all available
    drained = 0
    while not sink.q.empty():
        r = sink.q.get_nowait()
        engine.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)
        drained += 1
    ft.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await ft
    assert drained > 0
    snap = engine.snapshot()
    assert snap.breadth.raw_hz >= 0
    assert len(snap.top_movers) > 0
