# tests/feeds/test_equity_feed.py
import asyncio
import contextlib

import pytest
from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade

from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EXCHANGE, EquitySimFeed


@pytest.mark.asyncio
async def test_tps_hot_apply_changes_emit_rate(monkeypatch):
    """Setting feed.tps mid-run (the Settings hot-apply path) must change the
    per-batch emit size immediately — not just the attribute. Batch boundaries
    are observed deterministically by intercepting the inter-batch sleep."""
    sink = QueueSink(maxsize=100_000)
    feed = EquitySimFeed(sink, seed=1, ticks_per_sec=1000, batch_dt=0.01)  # 10/batch

    qsizes: list[int] = []  # cumulative queue size at each batch boundary
    real_sleep = asyncio.sleep

    async def boundary_sleep(_delay: float) -> None:
        qsizes.append(sink.q.qsize())
        if len(qsizes) == 2:
            feed.tps = 200  # hot-apply mid-run -> 2/batch
        if len(qsizes) >= 4:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr("entropy.feeds.equities.feed.asyncio.sleep", boundary_sleep)
    task = asyncio.create_task(feed.run())
    with contextlib.suppress(asyncio.CancelledError):
        await task

    batch_sizes = [b - a for a, b in zip([0, *qsizes[:-1]], qsizes, strict=True)]
    assert batch_sizes[:2] == [10, 10]   # tps=1000 * 0.01
    assert batch_sizes[2:] == [2, 2]     # tps=200 took effect on the NEXT batch


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
