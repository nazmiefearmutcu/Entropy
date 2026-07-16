import pytest
import json
from entropy.bot.orders import OrderIntent, OrderSide
from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import MEDIUM, make_custom
from entropy.bot.signals import Signal, SignalAction
from entropy.bot.ui.confirm import ConfirmRiskScreen
from entropy.bot.runner import BotRunner
from entropy.bot.config import BotConfig
from entropy.bot.ui.app import BotDashboard
from entropy.bot.ui.widgets import RiskBanner, TradeLog

def _sig(action: SignalAction, symbol: str = "SPY") -> Signal:
    return Signal(symbol=symbol, action=action, strength=1.0, reason="test", ts_ns=1, strategy="strat")

def test_volatility_history_and_scaling():
    # 1. Verify rolling history bounds & no duplicates on same timestamp
    rm = RiskManager(MEDIUM)
    rm.update_tick("SPY", 100.0, 1000)
    rm.update_tick("SPY", 100.0, 1000) # Duplicate timestamp
    assert len(rm.ticks_history["SPY"]) == 1
    
    # Fill rolling history up to 25 ticks, should keep only last 20
    for i in range(25):
        rm.update_tick("SPY", 100.0 + i, 1000 + i)
    assert len(rm.ticks_history["SPY"]) == 20
    assert rm.ticks_history["SPY"][0] == (1005, 105.0)
    assert rm.ticks_history["SPY"][-1] == (1024, 124.0)

    # 2. Verify fallback to 1.0 when less than 2 ticks
    rm2 = RiskManager(MEDIUM)
    rm2.update_tick("SPY", 100.0, 1000)
    sl, tp = rm2.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
    expected_sl = 100.0 * (1 - MEDIUM.stop_loss_pct / 100)
    expected_tp = 100.0 * (1 + MEDIUM.take_profit_pct / 100)
    assert sl == expected_sl
    assert tp == expected_tp

    # 3. Verify exact volatility scaling calculation
    rm3 = RiskManager(MEDIUM)
    rm3.update_tick("SPY", 100.0, 1000)
    rm3.update_tick("SPY", 110.0, 2000)
    # Ticks: [100.0, 110.0] -> Mean: 105.0. Std: (( (100-105)**2 + (110-105)**2 ) / 2)**0.5 = 5.0
    # Scale factor = 1.0 + 5.0 / 105.0 = 1.0 + 1/21 = 22/21 = 1.047619...
    scale = 22.0 / 21.0
    sl, tp = rm3.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
    expected_sl_scaled = 100.0 * (1 - (MEDIUM.stop_loss_pct * scale) / 100)
    expected_tp_scaled = 100.0 * (1 + (MEDIUM.take_profit_pct * scale) / 100)
    assert pytest.approx(sl) == expected_sl_scaled
    assert pytest.approx(tp) == expected_tp_scaled


def test_fat_finger_protection():
    # Size limit: qty * mark_px > 15% of equity or > $10,000
    p = Portfolio(100_000.0) # Equity = 100k, 15% = 15,000, 10,000 limit applies first
    
    # 1. Trigger > $10,000 limit
    prof_11k = make_custom(per_trade_pct=11.0, max_total_exposure_pct=100.0) # size = $11,000
    rm1 = RiskManager(prof_11k)
    d = rm1.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert not d.approved
    assert d.reason == "fat-finger limit exceeded"

    # 2. Trigger > 15% of equity limit (equity = 50,000, 15% = 7,500, size = 8,000)
    p2 = Portfolio(50_000.0)
    prof_16pct = make_custom(per_trade_pct=16.0, max_total_exposure_pct=100.0)
    rm2 = RiskManager(prof_16pct)
    d2 = rm2.evaluate(_sig(SignalAction.ENTER_LONG), p2, mark_px=100.0, ts_ns=1)
    assert not d2.approved
    assert d2.reason == "fat-finger limit exceeded"

    # 3. Allow safe order
    prof_safe = make_custom(per_trade_pct=5.0, max_total_exposure_pct=100.0) # size = 5% of 100k = $5,000
    rm3 = RiskManager(prof_safe)
    d3 = rm3.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert d3.approved


def test_price_deviation_guard():
    # Reject entry signal if mark price deviates from rolling average by > 3%
    rm = RiskManager(make_custom(min_volatility_pct=0.0))
    # Populate history with 20 ticks at 100.0
    for i in range(20):
        rm.update_tick("SPY", 100.0, 1000 + i)

    p = Portfolio(100_000.0)

    # 1. 3.1% deviation upward (103.1) -> Reject
    d1 = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=103.1, ts_ns=2000)
    assert not d1.approved
    assert d1.reason == "price deviation limit exceeded"

    # 2. 3.1% deviation downward (96.9) -> Reject
    d2 = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=96.9, ts_ns=2000)
    assert not d2.approved
    assert d2.reason == "price deviation limit exceeded"

    # 3. 2.9% deviation (102.9) -> Approve
    d3 = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=102.9, ts_ns=2000)
    assert d3.approved


def test_emergency_circuit_breaker():
    rm = RiskManager(MEDIUM)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.LONG, 10.0, 100.0, 99.0, 101.0, 1000, 0.0)
    
    # Trip manual breaker
    rm.trip()
    assert rm.circuit_tripped
    assert rm.halted

    # All signals evaluated (including exit signals) must be rejected
    d1 = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=2000)
    assert not d1.approved
    assert d1.reason == "halted: circuit breaker tripped"

    d2 = rm.evaluate(_sig(SignalAction.EXIT), p, mark_px=100.0, ts_ns=2000)
    assert not d2.approved
    assert d2.reason == "halted: circuit breaker tripped"

    # Verify close_all_positions returns close orders for all positions
    orders = rm.close_all_positions(p, ts_ns=2000)
    assert len(orders) == 1
    assert orders[0].symbol == "SPY"
    assert orders[0].side == OrderSide.SELL
    assert orders[0].intent == OrderIntent.CLOSE
    assert orders[0].qty == 10.0
    assert orders[0].price == 100.0


@pytest.mark.asyncio
async def test_bot_runner_circuit_breaker_liquidation(tmp_path):
    cfg = BotConfig(starting_cash=100_000.0, enable_crypto=False, enable_equities=False)
    runner = BotRunner(cfg, run_dir=str(tmp_path))
    
    # Manually open a position in portfolio
    runner.portfolio.open("SPY", PositionSide.LONG, 10.0, 100.0, 99.0, 101.0, 1000, 0.0)
    assert "SPY" in runner.portfolio.positions

    # Trip breaker
    runner.trip_circuit_breaker()
    assert runner.risk.circuit_tripped
    assert runner.risk.halted
    assert "SPY" not in runner.portfolio.positions # Liquidated!
    
    # Assert fills/events recorded
    with open(runner.ledger._events) as f:
        events = [json.loads(line) for line in f]
    halt_events = [e for e in events if e.get("kind") == "emergency_halt"]
    assert len(halt_events) == 1


@pytest.mark.asyncio
async def test_confirm_risk_screen_text(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    runner = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=runner)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ConfirmRiskScreen("frosty", lambda: None)
        app.push_screen(screen)
        await pilot.pause()
        static_widget = screen.query_one("Static")
        assert str(static_widget.render()) == "Are you sure with that 'frosty' risk management mode?"


@pytest.mark.asyncio
async def test_tui_circuit_breaker_trigger(tmp_path):
    cfg = BotConfig(starting_cash=100_000.0, enable_crypto=False, enable_equities=False)
    runner = BotRunner(cfg, run_dir=str(tmp_path))
    runner.portfolio.open("SPY", PositionSide.LONG, 10.0, 100.0, 99.0, 101.0, 1000, 0.0)

    app = BotDashboard(cfg, runner=runner)
    async with app.run_test() as pilot:
        await pilot.pause()
        
        # Verify initial state
        banner = app.query_one(RiskBanner)
        assert not banner.halted
        assert "SPY" in runner.portfolio.positions
        
        # Press 'k' key to trip circuit breaker
        await pilot.press("k")
        await pilot.pause()

        # Verify halted state
        assert runner.risk.circuit_tripped
        assert banner.halted
        assert banner.render_text() == "RISK PROFILE: HALTED"
        assert "SPY" not in runner.portfolio.positions
        
        # Check TradeLog widget updated
        log_widget = app.query_one(TradeLog)
        assert any("EMERGENCY HALT" in line.text for line in log_widget.lines)
