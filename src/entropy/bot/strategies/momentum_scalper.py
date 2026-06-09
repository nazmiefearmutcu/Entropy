from __future__ import annotations

from collections.abc import Sequence

from entropy.engine.events import DownMove, Event, SnapDrop, Spike, UpMove
from entropy.strategy.engine import Bar

from ..signals import Signal, SignalAction


class MomentumScalper:
    """Fast, short-window entries off the engine's momentum events. Exits are mechanical
    (risk-manager stop/take-profit), so this strategy only opens positions."""

    name = "momentum_scalper"

    def __init__(self, symbols: tuple[str, ...] | None = None, min_pct: float = 0.15) -> None:
        self.symbols = symbols  # None = trade every symbol
        self.min_pct = min_pct

    def warmup(self, bars: Sequence[Bar]) -> None:
        return None

    def on_tick(self, symbol: str, price: float, ts_ns: int,
                events: Sequence[Event]) -> list[Signal]:
        if self.symbols is not None and symbol not in self.symbols:
            return []
        out: list[Signal] = []
        for e in events:
            if e.symbol != symbol:
                continue
            if isinstance(e, (Spike, UpMove)) and e.pct >= self.min_pct:
                out.append(Signal(symbol=symbol, action=SignalAction.ENTER_LONG,
                                  strength=min(1.0, e.pct / 2.0),
                                  reason=f"momentum:{e.kind.value}:{e.pct:.2f}%",
                                  ts_ns=ts_ns, strategy=self.name))
            elif isinstance(e, (SnapDrop, DownMove)) and abs(e.pct) >= self.min_pct:
                out.append(Signal(symbol=symbol, action=SignalAction.ENTER_SHORT,
                                  strength=min(1.0, abs(e.pct) / 2.0),
                                  reason=f"momentum:{e.kind.value}:{e.pct:.2f}%",
                                  ts_ns=ts_ns, strategy=self.name))
        return out
