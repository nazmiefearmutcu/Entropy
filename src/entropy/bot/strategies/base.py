from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from entropy.engine.events import Event
from entropy.strategy.engine import Bar

from ..signals import Signal


class Strategy(Protocol):
    """Pluggable trading strategy. `on_tick` runs on the synchronous per-tick hot path,
    so it must be fast and side-effect free. It receives the engine events produced for
    this tick (not a full snapshot)."""

    name: str

    def on_tick(self, symbol: str, price: float, ts_ns: int,
                events: Sequence[Event]) -> list[Signal]: ...

    def warmup(self, bars: Sequence[Bar]) -> None: ...
