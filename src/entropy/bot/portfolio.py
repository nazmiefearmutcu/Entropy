from __future__ import annotations

import enum
from dataclasses import dataclass

import msgspec


class PositionSide(enum.StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass(slots=True)
class PositionState:
    symbol: str
    side: PositionSide
    qty: float
    entry_px: float
    stop_px: float
    tp_px: float
    entry_ts_ns: int
    realized_pnl: float = 0.0


class PositionView(msgspec.Struct, frozen=True):
    symbol: str
    side: PositionSide
    qty: float
    entry_px: float
    mark_px: float
    unrealized_pnl: float
    stop_px: float
    tp_px: float


class PortfolioSnapshot(msgspec.Struct, frozen=True):
    ts_ns: int
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    open_count: int
    positions: tuple[PositionView, ...]


def _gross(side: PositionSide, entry: float, mark: float, qty: float) -> float:
    return (mark - entry) * qty if side is PositionSide.LONG else (entry - mark) * qty


class Portfolio:
    def __init__(self, starting_cash: float) -> None:
        self.starting_cash = starting_cash
        self.realized_pnl = 0.0
        self.positions: dict[str, PositionState] = {}
        self._marks: dict[str, float] = {}
        self.day_start_equity = starting_cash

    def mark(self, symbol: str, price: float) -> None:
        self._marks[symbol] = price

    def mark_of(self, symbol: str) -> float:
        pos = self.positions.get(symbol)
        default = pos.entry_px if pos is not None else 0.0
        return self._marks.get(symbol, default)

    def open(self, symbol: str, side: PositionSide, qty: float, entry_px: float,
             stop_px: float, tp_px: float, ts_ns: int, fee: float) -> None:
        self.realized_pnl -= fee
        self.positions[symbol] = PositionState(
            symbol=symbol, side=side, qty=qty, entry_px=entry_px,
            stop_px=stop_px, tp_px=tp_px, entry_ts_ns=ts_ns,
        )
        self._marks[symbol] = entry_px

    def close(self, symbol: str, exit_px: float, ts_ns: int, fee: float) -> float:
        pos = self.positions.pop(symbol)
        realized = _gross(pos.side, pos.entry_px, exit_px, pos.qty) - fee
        self.realized_pnl += realized
        self._marks[symbol] = exit_px
        return realized

    def unrealized_pnl(self) -> float:
        return sum(
            _gross(p.side, p.entry_px, self._marks.get(s, p.entry_px), p.qty)
            for s, p in self.positions.items()
        )

    def equity(self) -> float:
        return self.starting_cash + self.realized_pnl + self.unrealized_pnl()

    def cash(self) -> float:
        return self.starting_cash + self.realized_pnl

    def exposure(self) -> float:
        return sum(self._marks.get(s, p.entry_px) * p.qty for s, p in self.positions.items())

    def daily_pnl(self) -> float:
        return self.equity() - self.day_start_equity

    def reset_day(self) -> None:
        self.day_start_equity = self.equity()

    def snapshot(self, ts_ns: int) -> PortfolioSnapshot:
        views = tuple(
            PositionView(
                symbol=p.symbol, side=p.side, qty=p.qty, entry_px=p.entry_px,
                mark_px=self._marks.get(p.symbol, p.entry_px),
                unrealized_pnl=_gross(
                    p.side, p.entry_px, self._marks.get(p.symbol, p.entry_px), p.qty
                ),
                stop_px=p.stop_px, tp_px=p.tp_px,
            )
            for p in self.positions.values()
        )
        return PortfolioSnapshot(
            ts_ns=ts_ns, cash=self.cash(), equity=self.equity(),
            realized_pnl=self.realized_pnl, unrealized_pnl=self.unrealized_pnl(),
            daily_pnl=self.daily_pnl(), open_count=len(self.positions), positions=views,
        )
