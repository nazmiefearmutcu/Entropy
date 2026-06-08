# src/entropy/feeds/bus.py
from __future__ import annotations

import asyncio
from typing import Any

from crypcodile.sink.base import Sink


class QueueSink(Sink):
    """Sink ABC impl that enqueues into a bounded asyncio.Queue.

    The kHz feed must never block on a slow UI, so put() is non-blocking and
    drops the OLDEST record on overflow (a scanner cares about latest price).
    close() inherits the default (await flush -> no-op): it must NOT clear the
    queue, because the TUI owns the queue lifecycle, not the sink.
    """
    def __init__(self, maxsize: int = 200_000) -> None:
        self.q: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    async def put(self, record: Any) -> None:
        try:
            self.q.put_nowait(record)
        except asyncio.QueueFull:
            try:
                self.q.get_nowait()
                self.dropped += 1
                self.q.put_nowait(record)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                self.dropped += 1

    async def flush(self) -> None:
        return None
