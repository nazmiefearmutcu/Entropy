"""Risk hints may only ever tighten.

The first test is the guarantee the rest of the codebase depends on: a signal
with no hints must produce exactly what today's code produces. Everything else
here checks that a hint can ask for less risk and never for more.
"""

from __future__ import annotations

import pytest

from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import get_profile
from entropy.bot.signals import Signal, SignalAction

_NS = 1_000_000_000


def make_manager() -> RiskManager:
    return RiskManager(get_profile("medium"))


def warm(risk: RiskManager, symbol: str, price: float, n: int = 10) -> int:
    """Feed n ticks with mild variation so the window guards have a sample.

    The amplitude is deliberately above MEDIUM's ``min_volatility_pct`` (0.15%).
    A +/-0.1% zig-zag reads as 0.083% volatility, which the PRE-EXISTING
    "sideways market" floor rejects before sizing is ever reached -- the tests
    below would then assert nothing about risk hints. +/-0.2% reads as 0.166%
    and clears the floor, while staying far inside the 3% deviation guard.
    """
    ts = 0
    for i in range(n):
        ts = (i + 1) * _NS // 10
        risk.update_tick(symbol, price * (1.0 + 0.002 * (i % 3 - 1)), ts)
    return ts


def enter(symbol: str, **hints) -> Signal:
    return Signal(symbol=symbol, action=SignalAction.ENTER_LONG, strength=1.0,
                  reason="test", ts_ns=0, strategy="test", **hints)


def test_absent_hints_reproduce_todays_stop_and_target():
    risk = make_manager()
    warm(risk, "SPY", 100.0)
    baseline = risk.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
    with_none = risk.stop_tp_prices(PositionSide.LONG, 100.0, "SPY",
                                    stop_pct=None, tp_pct=None)
    assert with_none == baseline


def test_absent_hints_reproduce_todays_size():
    risk = make_manager()
    ts = warm(risk, "SPY", 100.0)
    pf = Portfolio(100_000.0)
    decision = risk.evaluate(enter("SPY"), pf, 100.0, ts)
    assert decision.approved
    assert decision.order is not None
    expected_qty = (get_profile("medium").per_trade_pct / 100.0) * pf.equity() / 100.0
    assert decision.order.qty == pytest.approx(expected_qty)
    assert decision.order.stop_pct is None
    assert decision.order.tp_pct is None


def test_stop_hint_overrides_the_profile_percentage():
    risk = make_manager()
    warm(risk, "SPY", 100.0)
    stop, tp = risk.stop_tp_prices(PositionSide.LONG, 100.0, "SPY",
                                   stop_pct=3.0, tp_pct=6.0)
    assert stop == pytest.approx(97.0)
    assert tp == pytest.approx(106.0)


def test_stop_hint_applies_to_shorts_in_the_right_direction():
    risk = make_manager()
    warm(risk, "SPY", 100.0)
    stop, tp = risk.stop_tp_prices(PositionSide.SHORT, 100.0, "SPY",
                                   stop_pct=3.0, tp_pct=6.0)
    assert stop == pytest.approx(103.0)
    assert tp == pytest.approx(94.0)


def test_stop_hint_is_clamped_at_fifty_percent():
    risk = make_manager()
    warm(risk, "SPY", 100.0)
    stop, tp = risk.stop_tp_prices(PositionSide.LONG, 100.0, "SPY",
                                   stop_pct=80.0, tp_pct=90.0)
    assert stop == pytest.approx(50.0)
    assert tp == pytest.approx(150.0)


def test_one_hint_may_be_given_without_the_other():
    risk = make_manager()
    warm(risk, "SPY", 100.0)
    base_stop, base_tp = risk.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
    stop, tp = risk.stop_tp_prices(PositionSide.LONG, 100.0, "SPY", stop_pct=3.0)
    assert stop == pytest.approx(97.0)
    assert tp == pytest.approx(base_tp)
    assert base_stop != pytest.approx(97.0)


def test_smaller_size_hint_is_honoured():
    risk = make_manager()
    ts = warm(risk, "SPY", 100.0)
    pf = Portfolio(100_000.0)
    decision = risk.evaluate(enter("SPY", size_pct=0.5), pf, 100.0, ts)
    assert decision.approved
    assert decision.order is not None
    assert decision.order.qty == pytest.approx(0.5 / 100.0 * pf.equity() / 100.0)


def test_larger_size_hint_is_clamped_to_the_profile():
    risk = make_manager()
    ts = warm(risk, "SPY", 100.0)
    pf = Portfolio(100_000.0)
    profile_pct = get_profile("medium").per_trade_pct
    decision = risk.evaluate(enter("SPY", size_pct=99.0), pf, 100.0, ts)
    assert decision.approved
    assert decision.order is not None
    assert decision.order.qty == pytest.approx(profile_pct / 100.0 * pf.equity() / 100.0)


def test_a_zero_size_hint_is_not_silently_read_as_absent():
    """Kills `size_pct or per_trade_pct`: `or` treats 0.0 as absent and would open
    a full-size position where the correct code refuses to open one at all.

    Reachable through config -- `distribution.risk_hints` computes
    `size_pct = min(max_size_pct, risk_budget_pct / stop_frac)`, so a
    `risk_budget_pct` of 0 yields a legitimate 0.0.
    """
    risk = make_manager()
    ts = warm(risk, "SPY", 100.0)
    pf = Portfolio(100_000.0)
    decision = risk.evaluate(enter("SPY", size_pct=0.0), pf, 100.0, ts)
    assert not decision.approved
    assert "non-positive size" in decision.reason


def test_a_mild_over_ask_is_clamped_without_help_from_any_cap():
    """Kills a dropped `min()` in the band where every downstream cap still passes:
    3.0% of $100k is $3000, well under the $15k exposure cap and the $10k
    fat-finger cap, so only the clamp itself can reject the over-ask.
    """
    risk = make_manager()
    ts = warm(risk, "SPY", 100.0)
    pf = Portfolio(100_000.0)
    decision = risk.evaluate(enter("SPY", size_pct=3.0), pf, 100.0, ts)
    assert decision.approved
    assert decision.order is not None
    # 25.0, not 30.0 -- the profile's 2.5% wins over the strategy's 3.0% ask.
    assert decision.order.qty == pytest.approx(2.5 / 100.0 * pf.equity() / 100.0)


def test_hints_ride_from_the_signal_onto_the_order():
    risk = make_manager()
    ts = warm(risk, "SPY", 100.0)
    pf = Portfolio(100_000.0)
    decision = risk.evaluate(enter("SPY", stop_pct=2.0, tp_pct=4.0), pf, 100.0, ts)
    assert decision.approved
    assert decision.order is not None
    assert decision.order.stop_pct == pytest.approx(2.0)
    assert decision.order.tp_pct == pytest.approx(4.0)


def test_hints_do_not_bypass_any_guard():
    """A tiny size hint still cannot open a second position in the same symbol."""
    risk = make_manager()
    ts = warm(risk, "SPY", 100.0)
    pf = Portfolio(100_000.0)
    pf.open("SPY", PositionSide.LONG, 1.0, 100.0, 95.0, 110.0, ts, 0.0)
    decision = risk.evaluate(enter("SPY", size_pct=0.01), pf, 100.0, ts)
    assert not decision.approved
    assert "already in position" in decision.reason
