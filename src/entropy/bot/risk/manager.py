from __future__ import annotations

import msgspec

from ..orders import Order, OrderIntent, OrderSide
from ..portfolio import Portfolio, PositionSide, PositionState
from ..signals import Signal, SignalAction
from .profiles import RiskProfile

_NS_PER_S = 1_000_000_000

# Hard cap on retained ticks per symbol: a memory bound against tick storms
# denser than the profile's time window anticipates (oldest entries drop first).
_MAX_TICKS = 512

# Minimum in-window ticks for volatility / deviation statistics; below this the
# sample is "insufficient data" — entries proceed unfiltered (legacy <2-tick
# spirit) and exits are never blocked.
_MIN_WINDOW_TICKS = 5


class RiskDecision(msgspec.Struct, frozen=True):
    approved: bool
    order: Order | None
    reason: str


class RiskManager:
    def __init__(self, profile: RiskProfile) -> None:
        self.profile = profile
        self.halted = False
        self.circuit_tripped = False
        self._cooldown_until: dict[str, int] = {}
        self._order_seq = 0
        self.ticks_history: dict[str, list[tuple[int, float]]] = {}

    def set_profile(self, profile: RiskProfile) -> None:
        self.profile = profile

    def reset_day(self) -> None:
        """Clear the daily-loss kill-switch at the start of a new trading day.

        The kill-switch latches within a day (a tripped limit stays tripped until the
        day rolls over); the runner calls this on a UTC date change so the limit is
        genuinely *daily* rather than cumulative-since-start."""
        self.halted = False

    def update_tick(self, symbol: str, price: float, ts_ns: int) -> None:
        history = self.ticks_history.setdefault(symbol, [])
        if history and history[-1][0] == ts_ns:
            return  # ignore duplicate timestamps
        history.append((ts_ns, price))
        # Time-based pruning: the stats must see the profile's time horizon
        # (vol_window_s), not a fixed tick count that spans milliseconds at
        # sim tick rates.
        cutoff = ts_ns - int(self.profile.vol_window_s * _NS_PER_S)
        drop = 0
        for t, _ in history:
            if t >= cutoff:
                break
            drop += 1
        if drop:
            del history[:drop]
        if len(history) > _MAX_TICKS:
            del history[: len(history) - _MAX_TICKS]

    def _window_prices(self, symbol: str, ts_ns: int) -> list[float]:
        """Prices of ticks inside the profile's volatility window ending at ts_ns."""
        cutoff = ts_ns - int(self.profile.vol_window_s * _NS_PER_S)
        return [px for t, px in self.ticks_history.get(symbol, []) if t >= cutoff]

    def trip(self) -> None:
        self.circuit_tripped = True
        self.halted = True

    def close_all_positions(self, portfolio: Portfolio, ts_ns: int) -> list[Order]:
        orders: list[Order] = []
        for pos in list(portfolio.positions.values()):
            mark_px = portfolio.mark_of(pos.symbol)
            orders.append(
                self._close_order(pos, mark_px, ts_ns, "circuit_breaker", OrderIntent.CLOSE)
            )
        return orders

    def _next_id(self) -> str:
        self._order_seq += 1
        return f"o{self._order_seq}"

    def stop_tp_prices(
        self, side: PositionSide, entry_px: float, symbol: str | None = None
    ) -> tuple[float, float]:
        p = self.profile
        scale_factor = 1.0
        if symbol is not None and symbol in self.ticks_history:
            history = self.ticks_history[symbol]
            if len(history) >= 2:
                prices = [p[1] for p in history]
                mean = sum(prices) / len(prices)
                if mean > 0:
                    variance = sum((x - mean) ** 2 for x in prices) / len(prices)
                    std = variance ** 0.5
                    scale_factor = 1.0 + std / mean

        stop_loss_pct = p.stop_loss_pct * scale_factor
        take_profit_pct = p.take_profit_pct * scale_factor

        if side is PositionSide.LONG:
            return entry_px * (1 - stop_loss_pct / 100), entry_px * (1 + take_profit_pct / 100)
        return entry_px * (1 + stop_loss_pct / 100), entry_px * (1 - take_profit_pct / 100)

    def _kill_switch(self, portfolio: Portfolio) -> bool:
        limit = -(self.profile.max_daily_loss_pct / 100.0) * portfolio.day_start_equity
        if portfolio.daily_pnl() <= limit:
            self.halted = True
        return self.halted

    def evaluate(self, signal: Signal, portfolio: Portfolio,
                 mark_px: float, ts_ns: int) -> RiskDecision:
        if self.circuit_tripped:
            return RiskDecision(False, None, "halted: circuit breaker tripped")

        pos = portfolio.positions.get(signal.symbol)

        # Risk-REDUCING exits are always allowed — even after the kill-switch halts new
        # trading you must be able to close (de-risk) an open position. Only risk-
        # INCREASING entries are gated by the daily-loss kill-switch below.
        if signal.action is SignalAction.EXIT:
            if pos is None:
                return RiskDecision(False, None, "no open position to exit")
            close_ord = self._close_order(pos, mark_px, ts_ns, signal.strategy)
            return RiskDecision(True, close_ord, "exit")

        if self._kill_switch(portfolio):
            return RiskDecision(False, None, "halted: daily loss limit reached")

        if pos is not None:
            return RiskDecision(False, None, f"already in position for {signal.symbol}")
        if len(portfolio.positions) >= self.profile.max_concurrent:
            return RiskDecision(False, None,
                                f"max concurrent positions ({self.profile.max_concurrent}) reached")
        if ts_ns < self._cooldown_until.get(signal.symbol, 0):
            return RiskDecision(False, None, "cooldown active")
        if mark_px <= 0:
            return RiskDecision(False, None, "invalid mark price")

        volatility_pct: float | None = None
        prices = self._window_prices(signal.symbol, ts_ns)

        # Volatility Floor Filter (over the profile's time window)
        if (
            signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT)
            and len(prices) >= _MIN_WINDOW_TICKS
        ):
            mean = sum(prices) / len(prices)
            if mean > 0:
                variance = sum((x - mean) ** 2 for x in prices) / len(prices)
                std = variance ** 0.5
                volatility_pct = (std / mean) * 100
                if volatility_pct < self.profile.min_volatility_pct:
                    return RiskDecision(
                        False, None, "sideways market: volatility below threshold"
                    )

        # Slippage / Price Deviation Guard (same window, same sample-size floor)
        if len(prices) >= _MIN_WINDOW_TICKS:
            avg = sum(prices) / len(prices)
            if avg > 0 and abs(mark_px - avg) / avg > 0.03:
                return RiskDecision(False, None, "price deviation limit exceeded")

        equity = portfolio.equity()
        qty = (self.profile.per_trade_pct / 100.0) * equity / mark_px
        if qty <= 0:
            return RiskDecision(False, None, "non-positive size")

        projected = portfolio.exposure() + qty * mark_px
        if projected > (self.profile.max_total_exposure_pct / 100.0) * equity:
            return RiskDecision(False, None, "exposure cap exceeded")

        # Fat-finger protection
        order_size = qty * mark_px
        if order_size > 0.15 * equity or order_size > 10000.0:
            return RiskDecision(False, None, "fat-finger limit exceeded")

        side = OrderSide.BUY if signal.action is SignalAction.ENTER_LONG else OrderSide.SELL
        order = Order(id=self._next_id(), symbol=signal.symbol, side=side,
                      intent=OrderIntent.OPEN, qty=qty, price=mark_px, ts_ns=ts_ns,
                      strategy=signal.strategy)

        if volatility_pct is not None and volatility_pct < 0.30:
            scale_factor = 10.0 if volatility_pct <= 0.0 else min(0.30 / volatility_pct, 10.0)
        else:
            scale_factor = 1.0

        cooldown_ns = int(self.profile.cooldown_s * scale_factor * _NS_PER_S)
        self._cooldown_until[signal.symbol] = ts_ns + cooldown_ns
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
