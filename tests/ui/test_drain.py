# tests/ui/test_drain.py
"""run_drain must hand the event loop back periodically under a queue backlog:
asyncio.Queue.get() returns WITHOUT yielding while items are available, so a
sustained kHz burst used to starve every other task (10 Hz UI timers included)."""
from __future__ import annotations

import asyncio

import pytest
from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp


def _trade(i: int) -> Trade:
    return Trade(exchange="sim-equity", symbol="ZZZ", symbol_raw="ZZZ",
                 exchange_ts=i, local_ts=i, id=f"t{i}", price=100.0, amount=1.0,
                 side=Side.BUY)


@pytest.mark.asyncio
async def test_drain_yields_to_competing_tasks_under_backlog():
    app = EntropyApp(AppConfig(enable_crypto=False, enable_equities=False))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        processed = 0
        orig = app.engine.on_trade

        def counting(*a, **k):
            nonlocal processed
            processed += 1
            return orig(*a, **k)

        app.engine.on_trade = counting  # type: ignore[method-assign]

        # Prime: park the drain worker on q.get() with an empty queue.
        app._sink.q.put_nowait(_trade(0))
        for _ in range(100):
            if processed >= 1:
                break
            await asyncio.sleep(0)
        assert processed == 1

        # Backlog of 1000 records, then a competitor scheduled AFTER the drain's
        # wakeup: without the periodic yield the drain consumes the whole backlog
        # in one scheduler step, so the competitor would first observe 1001.
        for i in range(1, 1001):
            app._sink.q.put_nowait(_trade(i))
        seen_at: list[int] = []

        async def competitor() -> None:
            seen_at.append(processed)

        task = asyncio.create_task(competitor())
        for _ in range(5000):
            if processed >= 1001 and task.done():
                break
            await asyncio.sleep(0)
        await task
        assert processed == 1001  # backlog fully drained
        assert seen_at and seen_at[0] < 1001, (
            f"drain starved the loop: competitor first ran after {seen_at[0]} records"
        )
