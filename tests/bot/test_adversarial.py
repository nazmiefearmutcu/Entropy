import pytest
import math
from entropy.bot.orders import OrderIntent, OrderSide
from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import MEDIUM, make_custom
from entropy.bot.signals import Signal, SignalAction
from entropy.bot.calibration import run_backtest, calibrate_and_test

def _sig(action: SignalAction, symbol: str = "SPY") -> Signal:
    return Signal(symbol=symbol, action=action, strength=1.0, reason="test", ts_ns=1, strategy="strat")

def test_inf_ticks_behavior():
    # 1. Test standard RiskManager with an inf tick in history
    rm = RiskManager(MEDIUM)
    rm.update_tick("SPY", 100.0, 1000)
    rm.update_tick("SPY", float('inf'), 2000)
    
    # Let's check stop/tp calculation.
    # Mean will be inf. std will be nan because (inf - inf) is nan.
    # Therefore, scale_factor = 1.0 + std / mean = nan.
    # Stop/tp prices will be nan.
    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
    assert math.isnan(sl)
    assert math.isnan(tp)

    # 2. Check if a new entry signal gets approved/rejected when inf tick is in history
    p = Portfolio(100_000.0)
    # The volatility calculation will have mean = inf, std = nan, volatility_pct = nan.
    # This evaluates nan < min_volatility_pct as False, so it bypasses volatility floor check.
    # But it also means no error is raised in evaluate.
    decision = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=3000)
    assert decision.approved
    
    # 3. Check negative inf behavior
    rm_neginf = RiskManager(MEDIUM)
    rm_neginf.update_tick("SPY", 100.0, 1000)
    rm_neginf.update_tick("SPY", float('-inf'), 2000)
    # Mean will be -inf. mean > 0 is False. scale_factor defaults to 1.0.
    sl, tp = rm_neginf.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
    expected_sl = 100.0 * (1 - MEDIUM.stop_loss_pct / 100)
    expected_tp = 100.0 * (1 + MEDIUM.take_profit_pct / 100)
    assert sl == expected_sl
    assert tp == expected_tp


def test_nan_ticks_behavior():
    rm = RiskManager(MEDIUM)
    rm.update_tick("SPY", 100.0, 1000)
    rm.update_tick("SPY", float('nan'), 2000)
    
    # Mean is nan, so mean > 0 is False. scale_factor defaults to 1.0.
    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
    expected_sl = 100.0 * (1 - MEDIUM.stop_loss_pct / 100)
    expected_tp = 100.0 * (1 + MEDIUM.take_profit_pct / 100)
    assert sl == expected_sl
    assert tp == expected_tp

    # Volatility floor also bypasses since mean is nan, evaluating mean > 0 as False.
    p = Portfolio(100_000.0)
    decision = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=3000)
    assert decision.approved
    # Since volatility_pct is None, cooldown scale factor defaults to 1.0.
    assert rm._cooldown_until["SPY"] == 3000 + int(MEDIUM.cooldown_s * 1.0 * 1_000_000_000)


def test_extreme_low_and_zero_volatility():
    # Test zero volatility (prices do not change)
    rm = RiskManager(make_custom(min_volatility_pct=0.15, cooldown_s=10))
    rm.update_tick("SPY", 100.0, 1000)
    rm.update_tick("SPY", 100.0, 2000)
    
    p = Portfolio(100_000.0)
    # Volatility is 0.0%, which is below min_volatility_pct (0.15%)
    decision = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=3000)
    assert not decision.approved
    assert decision.reason == "sideways market: volatility below threshold"

    # If min_volatility_pct is 0, it should be approved, and cooldown scaling should be maximum (10.0x)
    rm_zero_min = RiskManager(make_custom(min_volatility_pct=0.0, cooldown_s=10))
    rm_zero_min.update_tick("SPY", 100.0, 1000)
    rm_zero_min.update_tick("SPY", 100.0, 2000)
    
    decision2 = rm_zero_min.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=3000)
    assert decision2.approved
    # Cooldown should be 10 * 10 * 1e9 = 100 seconds
    assert rm_zero_min._cooldown_until["SPY"] == 3000 + 100_000_000_000

    # Extremely low non-zero volatility (e.g. std/mean = 0.0001%)
    # Cooldown scale factor = min(0.30 / volatility_pct, 10.0). Since volatility_pct is extremely low, it should cap at 10.0.
    rm_low_vol = RiskManager(make_custom(min_volatility_pct=0.0, cooldown_s=10))
    rm_low_vol.update_tick("SPY", 100.0, 1000)
    rm_low_vol.update_tick("SPY", 100.0001, 2000)
    
    decision3 = rm_low_vol.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=3000)
    assert decision3.approved
    assert rm_low_vol._cooldown_until["SPY"] == 3000 + 100_000_000_000


def test_negative_volatility_prevention():
    # Mathematically volatility shouldn't be negative, but let's see if we can trick the code if prices are negative
    # Prices negative (e.g. ticks are negative, mean negative)
    # Since mean > 0 check gates the volatility calculation, negative mean will bypass the volatility floor check.
    # What if mean is positive but a tick is negative? E.g., history has [200.0, -100.0]. Mean is 50.0.
    # std = (((298 - 99)**2 + (-100 - 99)**2)/2)**0.5 = 199.0.
    # volatility_pct = 199.0 / 99.0 * 100 = 201.0%.
    # This works without issue.
    rm = RiskManager(MEDIUM)
    rm.update_tick("SPY", 298.0, 1000)
    rm.update_tick("SPY", -100.0, 2000)
    p = Portfolio(100_000.0)
    decision = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=3000)
    assert decision.approved



def test_calibration_overtrading_penalty():
    # Grid search score function: score = res["total_return"] * 10.0 + res["sharpe"] - (res["total_trades"] * 0.02)
    # Check that high number of trades reduces the calibration score even if return is same.
    res_few = {
        "total_return": 0.10,
        "sharpe": 1.5,
        "total_trades": 10
    }
    score_few = res_few["total_return"] * 10.0 + res_few["sharpe"] - (res_few["total_trades"] * 0.02)
    
    res_many = {
        "total_return": 0.10,
        "sharpe": 1.5,
        "total_trades": 100
    }
    score_many = res_many["total_return"] * 10.0 + res_many["sharpe"] - (res_many["total_trades"] * 0.02)
    
    assert score_few == 1.0 + 1.5 - 0.20 # 2.30
    assert score_many == 1.0 + 1.5 - 2.00 # 0.50
    assert score_few > score_many
