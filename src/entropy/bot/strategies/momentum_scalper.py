from __future__ import annotations

from collections.abc import Sequence

from entropy.engine.events import DownMove, Event, SnapDrop, Spike, UpMove
from entropy.strategy.engine import Bar

from ..costs import CostModel
from ..signals import Signal, SignalAction


class MomentumScalper:
    """Fast, short-window entries off the engine's momentum events. Exits are mechanical
    (risk-manager stop/take-profit), so this strategy only opens positions."""

    name = "momentum_scalper"

    def __init__(self, symbols: tuple[str, ...] | None = None, min_pct: float = 0.15,
                 *, costs: CostModel | None = None, cost_edge_mult: float = 2.0) -> None:
        self.symbols = symbols  # None = trade every symbol
        self.min_pct = min_pct
        self.costs = costs
        self.cost_edge_mult = cost_edge_mult

    def warmup(self, bars: Sequence[Bar]) -> None:
        return None

    def on_position_closed(self, symbol: str, reason: str) -> None:
        return None  # stateless: it never believes it holds anything

    def on_tick(self, symbol: str, price: float, ts_ns: int,
                events: Sequence[Event]) -> list[Signal]:
        if self.symbols is not None and symbol not in self.symbols:
            return []
        effective_min = self.min_pct
        if self.costs is not None:
            effective_min = max(self.min_pct,
                                self.costs.minimum_move(symbol, self.cost_edge_mult) * 100.0)
        out: list[Signal] = []
        for e in events:
            if e.symbol != symbol:
                continue
            if isinstance(e, (Spike, UpMove)) and e.pct >= effective_min:
                out.append(Signal(symbol=symbol, action=SignalAction.ENTER_LONG,
                                  strength=min(1.0, e.pct / 2.0),
                                  reason=f"momentum:{e.kind.value}:{e.pct:.2f}%",
                                  ts_ns=ts_ns, strategy=self.name))
            elif isinstance(e, (SnapDrop, DownMove)) and abs(e.pct) >= effective_min:
                out.append(Signal(symbol=symbol, action=SignalAction.ENTER_SHORT,
                                  strength=min(1.0, abs(e.pct) / 2.0),
                                  reason=f"momentum:{e.kind.value}:{e.pct:.2f}%",
                                  ts_ns=ts_ns, strategy=self.name))
        return out
