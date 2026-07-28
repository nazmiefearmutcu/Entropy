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

    def on_position_closed(self, symbol: str, reason: str) -> None:
        """Tell the strategy a position it believes in no longer exists.

        Strategies track what THEY have signalled; the portfolio tracks what is
        actually open. Those two drift apart whenever the position ends for a
        reason the strategy did not ask for — a mechanical stop or take-profit,
        the circuit breaker, or a risk rejection that stopped the entry from
        happening at all.

        Left unreported, the drift is silent and lasting: a strategy that thinks
        it is long will not open again until its own exit condition fires, so a
        single take-profit could mute it for the rest of a trend. The runner
        calls this on every such close.
        """
        ...
