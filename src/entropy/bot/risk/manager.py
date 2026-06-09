from __future__ import annotations

import msgspec

from ..orders import Order, OrderIntent, OrderSide
from ..portfolio import Portfolio, PositionSide, PositionState
from ..signals import Signal, SignalAction
from .profiles import RiskProfile

_NS_PER_S = 1_000_000_000


class RiskDecision(msgspec.Struct, frozen=True):
    approved: bool
    order: Order | None
    reason: str


class RiskManager:
    def __init__(self, profile: RiskProfile) -> None:
        self.profile = profile
        self.halted = False
        self._cooldown_until: dict[str, int] = {}
        self._order_seq = 0

    def set_profile(self, profile: RiskProfile) -> None:
        self.profile = profile

    def _next_id(self) -> str:
        self._order_seq += 1
        return f"o{self._order_seq}"

    def stop_tp_prices(self, side: PositionSide, entry_px: float) -> tuple[float, float]:
        p = self.profile
        if side is PositionSide.LONG:
            return entry_px * (1 - p.stop_loss_pct / 100), entry_px * (1 + p.take_profit_pct / 100)
        return entry_px * (1 + p.stop_loss_pct / 100), entry_px * (1 - p.take_profit_pct / 100)

    def _kill_switch(self, portfolio: Portfolio) -> bool:
        limit = -(self.profile.max_daily_loss_pct / 100.0) * portfolio.day_start_equity
        if portfolio.daily_pnl() <= limit:
            self.halted = True
        return self.halted

    def evaluate(self, signal: Signal, portfolio: Portfolio,
                 mark_px: float, ts_ns: int) -> RiskDecision:
        if self._kill_switch(portfolio):
            return RiskDecision(False, None, "halted: daily loss limit reached")

        pos = portfolio.positions.get(signal.symbol)
        if signal.action is SignalAction.EXIT:
            if pos is None:
                return RiskDecision(False, None, "no open position to exit")
            close_ord = self._close_order(pos, mark_px, ts_ns, signal.strategy)
            return RiskDecision(True, close_ord, "exit")

        if pos is not None:
            return RiskDecision(False, None, f"already in position for {signal.symbol}")
        if len(portfolio.positions) >= self.profile.max_concurrent:
            return RiskDecision(False, None,
                                f"max concurrent positions ({self.profile.max_concurrent}) reached")
        if ts_ns < self._cooldown_until.get(signal.symbol, 0):
            return RiskDecision(False, None, "cooldown active")
        if mark_px <= 0:
            return RiskDecision(False, None, "invalid mark price")

        equity = portfolio.equity()
        qty = (self.profile.per_trade_pct / 100.0) * equity / mark_px
        if qty <= 0:
            return RiskDecision(False, None, "non-positive size")
        projected = portfolio.exposure() + qty * mark_px
        if projected > (self.profile.max_total_exposure_pct / 100.0) * equity:
            return RiskDecision(False, None, "exposure cap exceeded")

        side = OrderSide.BUY if signal.action is SignalAction.ENTER_LONG else OrderSide.SELL
        order = Order(id=self._next_id(), symbol=signal.symbol, side=side,
                      intent=OrderIntent.OPEN, qty=qty, price=mark_px, ts_ns=ts_ns,
                      strategy=signal.strategy)
        self._cooldown_until[signal.symbol] = ts_ns + int(self.profile.cooldown_s * _NS_PER_S)
        return RiskDecision(True, order, "approved")

    def _close_order(self, pos: PositionState, mark_px: float, ts_ns: int,
                     strategy: str, intent: OrderIntent = OrderIntent.CLOSE) -> Order:
        side = OrderSide.SELL if pos.side is PositionSide.LONG else OrderSide.BUY
        return Order(id=self._next_id(), symbol=pos.symbol, side=side, intent=intent,
                     qty=pos.qty, price=mark_px, ts_ns=ts_ns, strategy=strategy)

    def check_exits(self, portfolio: Portfolio, ts_ns: int) -> list[Order]:
        out: list[Order] = []
        for pos in list(portfolio.positions.values()):
            mk = portfolio.mark_of(pos.symbol)
            if pos.side is PositionSide.LONG:
                hit_stop, hit_tp = mk <= pos.stop_px, mk >= pos.tp_px
            else:
                hit_stop, hit_tp = mk >= pos.stop_px, mk <= pos.tp_px
            if hit_stop:
                out.append(self._close_order(pos, mk, ts_ns, "risk", OrderIntent.STOP))
            elif hit_tp:
                out.append(self._close_order(pos, mk, ts_ns, "risk", OrderIntent.TAKE_PROFIT))
        return out
