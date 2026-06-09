from entropy.bot.orders import OrderIntent, OrderSide
from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import BALANCED, CONSERVATIVE, make_custom
from entropy.bot.signals import Signal, SignalAction


def _sig(action: SignalAction, symbol: str = "SPY") -> Signal:
    return Signal(symbol=symbol, action=action, strength=1.0, reason="t", ts_ns=1, strategy="s")


def test_enter_long_sizes_from_per_trade_pct():
    rm = RiskManager(BALANCED)  # 2.5% per trade
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert d.approved
    assert d.order is not None
    assert d.order.side is OrderSide.BUY
    assert d.order.intent is OrderIntent.OPEN
    assert d.order.qty == 0.025 * 100_000.0 / 100.0  # 25 shares


def test_reject_when_already_in_position():
    rm = RiskManager(BALANCED)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.LONG, 1.0, 100.0, 99.0, 101.0, 1, 0.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=2)
    assert not d.approved
    assert "already" in d.reason


def test_reject_on_max_concurrent():
    rm = RiskManager(CONSERVATIVE)  # max 2
    p = Portfolio(100_000.0)
    p.open("A", PositionSide.LONG, 1.0, 10.0, 9.0, 11.0, 1, 0.0)
    p.open("B", PositionSide.LONG, 1.0, 10.0, 9.0, 11.0, 1, 0.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG, "C"), p, mark_px=10.0, ts_ns=2)
    assert not d.approved
    assert "concurrent" in d.reason


def test_cooldown_blocks_immediate_reentry():
    rm = RiskManager(make_custom(cooldown_s=10.0, max_total_exposure_pct=100.0))
    p = Portfolio(100_000.0)
    d1 = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=0)
    assert d1.approved
    # simulate the position was opened+closed, then re-signal within cooldown
    d2 = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=5_000_000_000)
    assert not d2.approved
    assert "cooldown" in d2.reason


def test_exposure_cap_rejects():
    rm = RiskManager(make_custom(per_trade_pct=50.0, max_total_exposure_pct=10.0))
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert not d.approved
    assert "exposure" in d.reason


def test_exit_signal_closes_existing_position():
    rm = RiskManager(BALANCED)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.LONG, 1.0, 100.0, 99.0, 101.0, 1, 0.0)
    d = rm.evaluate(_sig(SignalAction.EXIT), p, mark_px=105.0, ts_ns=2)
    assert d.approved
    assert d.order is not None
    assert d.order.intent is OrderIntent.CLOSE
    assert d.order.side is OrderSide.SELL  # closing a long


def test_exit_with_no_position_rejected():
    rm = RiskManager(BALANCED)
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.EXIT), p, mark_px=105.0, ts_ns=2)
    assert not d.approved


def test_stop_tp_prices_for_long_and_short():
    rm = RiskManager(BALANCED)  # 1% stop, 2% tp
    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0)
    assert sl == 99.0 and tp == 102.0
    sl, tp = rm.stop_tp_prices(PositionSide.SHORT, 100.0)
    assert sl == 101.0 and tp == 98.0


def test_check_exits_triggers_stop_for_long():
    rm = RiskManager(BALANCED)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.LONG, 10.0, 100.0, stop_px=99.0, tp_px=102.0, ts_ns=1, fee=0.0)
    p.mark("SPY", 98.5)  # below stop
    orders = rm.check_exits(p, ts_ns=2)
    assert len(orders) == 1
    assert orders[0].intent is OrderIntent.STOP
    assert orders[0].side is OrderSide.SELL


def test_kill_switch_halts_after_daily_loss():
    rm = RiskManager(make_custom(max_daily_loss_pct=5.0, max_total_exposure_pct=100.0))
    p = Portfolio(1000.0)
    # force a -6% day via a realized loss
    p.realized_pnl = -60.0
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=10.0, ts_ns=1)
    assert not d.approved
    assert "halt" in d.reason.lower()
    assert rm.halted


def test_exit_allowed_even_when_halted():
    # A kill-switch must block new RISK but still permit closing (de-risking) a position.
    rm = RiskManager(make_custom(max_daily_loss_pct=5.0, max_total_exposure_pct=100.0))
    p = Portfolio(1000.0)
    p.open("SPY", PositionSide.LONG, 1.0, 100.0, 99.0, 101.0, 1, 0.0)
    p.realized_pnl = -60.0  # -6% day → past the 5% limit
    blocked = rm.evaluate(_sig(SignalAction.ENTER_LONG, "X"), p, mark_px=10.0, ts_ns=2)
    assert not blocked.approved and "halt" in blocked.reason.lower()
    exit_dec = rm.evaluate(_sig(SignalAction.EXIT, "SPY"), p, mark_px=95.0, ts_ns=3)
    assert exit_dec.approved
    assert exit_dec.order is not None
    assert exit_dec.order.intent is OrderIntent.CLOSE


def test_reset_day_clears_halt_and_resumes_trading():
    rm = RiskManager(make_custom(max_daily_loss_pct=5.0, max_total_exposure_pct=100.0))
    p = Portfolio(1000.0)
    p.realized_pnl = -60.0
    rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=10.0, ts_ns=1)  # trips the halt
    assert rm.halted
    p.reset_day()   # new trading day: baseline rolls forward
    rm.reset_day()  # kill-switch cleared
    assert not rm.halted
    resumed = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=10.0, ts_ns=2)
    assert resumed.approved  # trading resumes after rollover
