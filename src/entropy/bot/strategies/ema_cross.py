from __future__ import annotations

from collections.abc import Sequence

from entropy.engine.events import Event
from entropy.strategy.engine import Bar, EventKind, StrategyConfig
from entropy.strategy.engine import Strategy as _EmaCore

from ..signals import Signal, SignalAction

_MAP = {
    EventKind.OPEN_LONG: SignalAction.ENTER_LONG,
    EventKind.OPEN_SHORT: SignalAction.ENTER_SHORT,
    EventKind.CLOSE_LONG: SignalAction.EXIT,
    EventKind.CLOSE_SHORT: SignalAction.EXIT,
}


class EmaCrossStrategy:
    name = "ema_cross"

    def __init__(self, symbol: str, fast: int = 9, slow: int = 21) -> None:
        self.symbol = symbol
        self._core = _EmaCore(StrategyConfig(symbol=symbol, fast=fast, slow=slow))

    def warmup(self, bars: Sequence[Bar]) -> None:
        self._core.warmup(bars)

    def on_tick(self, symbol: str, price: float, ts_ns: int,
                events: Sequence[Event]) -> list[Signal]:
        if symbol != self.symbol:
            return []
        out: list[Signal] = []
        for se in self._core.on_price(symbol, price, ts_ns):
            action = _MAP.get(se.kind)
            if action is not None:
                out.append(Signal(symbol=symbol, action=action, strength=1.0,
                                  reason=f"ema_cross:{se.kind.value}", ts_ns=ts_ns,
                                  strategy=self.name))
        return out
