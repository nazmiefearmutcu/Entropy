# tests/feeds/test_bus.py
import asyncio
import pytest
from entropy.feeds.bus import QueueSink

@pytest.mark.asyncio
async def test_put_enqueues_record():
    sink = QueueSink(maxsize=4)
    await sink.put("a")
    assert sink.q.get_nowait() == "a"
    assert sink.dropped == 0

@pytest.mark.asyncio
async def test_drop_oldest_on_overflow():
    sink = QueueSink(maxsize=2)
    for x in ("a", "b", "c"):   # c overflows -> drops oldest "a"
        await sink.put(x)
    drained = [sink.q.get_nowait() for _ in range(sink.q.qsize())]
    assert drained == ["b", "c"]
    assert sink.dropped == 1

@pytest.mark.asyncio
async def test_close_is_nondestructive():
    sink = QueueSink(maxsize=2)
    await sink.put("a")
    await sink.close()                 # inherited: flush -> no-op
    assert sink.q.get_nowait() == "a"
