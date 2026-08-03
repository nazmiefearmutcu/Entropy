"""Cost-aware gating: executor, strategy and risk-layer behavior with a cost
model, plus calibration metrics. Every gate must be a no-op without a cost
model (that contract keeps the flat-fee paper path byte-identical)."""

from __future__ import annotations

import random

import pytest

from entropy.bot.calibration import generate_ticks, run_backtest
from entropy.bot.config import MarketCostConfig
from entropy.bot.costs import CostModel, MarketClass, MarketCosts
from entropy.bot.execution.paper import PaperExecutor
from entropy.bot.orders import Order, OrderIntent, OrderSide
from entropy.bot.portfolio import Portfolio
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import make_custom
from entropy.bot.signals import Signal, SignalAction
from entropy.bot.strategies.consensus import ConsensusStrategy, Votes


def _sig(action: SignalAction, symbol: str = "SPY") -> Signal:
    return Signal(symbol=symbol, action=action, strength=1.0, reason="t",
                  ts_ns=1, strategy="s")


def _spot_costs() -> CostModel:
    return CostModel(flat_fee_bps=1.0, flat_slippage_bps=1.0,
                     market={MarketClass.CRYPTO_SPOT: MarketCosts(10.0, 3.0)})


# ---- paper executor --------------------------------------------------------


def test_paper_executor_charges_per_market_costs():
    ex = PaperExecutor(fee_bps=1.0, slippage_bps=1.0, cost_model=_spot_costs())
    spot = Order(id="1", symbol="SOLUSDT", side=OrderSide.BUY, intent=OrderIntent.OPEN,
                 qty=10.0, price=100.0, ts_ns=1, strategy="s")
    equity = Order(id="2", symbol="AAPL", side=OrderSide.BUY, intent=OrderIntent.OPEN,
                   qty=10.0, price=100.0, ts_ns=1, strategy="s")
    f_spot = ex.submit(spot)
    f_equity = ex.submit(equity)
    # Spot: 10 bps fee + 3 bps adverse slip; equity: flat 1/1.
    assert f_spot.fee == pytest.approx(abs(f_spot.price * 10.0) * 10.0 / 10_000.0)
    assert f_spot.slippage == pytest.approx(100.0 * 3.0 / 10_000.0)
    assert f_equity.fee == pytest.approx(abs(f_equity.price * 10.0) * 1.0 / 10_000.0)
    assert f_equity.slippage == pytest.approx(100.0 * 1.0 / 10_000.0)


def test_paper_executor_without_cost_model_unchanged():
    ex = PaperExecutor(fee_bps=5.0, slippage_bps=2.0)
    order = Order(id="1", symbol="SOLUSDT", side=OrderSide.SELL, intent=OrderIntent.OPEN,
                  qty=10.0, price=100.0, ts_ns=1, strategy="s")
    fill = ex.submit(order)
    assert fill.price == pytest.approx(100.0 - 100.0 * 2.0 / 10_000.0)
    assert fill.fee == pytest.approx(abs(fill.price * 10.0) * 5.0 / 10_000.0)


# ---- risk manager gates ----------------------------------------------------


def test_cost_to_stop_gate_rejects_churn_trades():
    cm = _spot_costs()  # C = 26 bps
    rm = RiskManager(make_custom(stop_loss_pct=0.05), cost_model=cm)  # 5 bps stop
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert not d.approved
    assert "cost-to-stop ratio too high" in d.reason


def test_take_profit_must_beat_round_trip_cost():
    cm = _spot_costs()  # C = 26 bps
    rm = RiskManager(make_custom(take_profit_pct=0.01), cost_model=cm)  # 1 bp TP
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert not d.approved
    assert "take-profit below round-trip cost" in d.reason


def test_cost_gates_are_noop_without_cost_model():
    rm = RiskManager(make_custom(stop_loss_pct=0.05, take_profit_pct=0.01))
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert d.approved  # flat path: the same degenerate profile passes


def test_sane_profile_passes_cost_gates():
    cm = _spot_costs()
    rm = RiskManager(make_custom(stop_loss_pct=1.0, take_profit_pct=2.0),
                     cost_model=cm)
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert d.approved  # 26 bps cost vs 100 bps stop / 200 bps TP


# ---- consensus strategy gates ----------------------------------------------


def _feed(strat, symbol, closes):
    events = []
    for i, px in enumerate(closes):
        ts = i * 5_000_000_000 + 1
        events += strat.on_tick(symbol, px, ts, events=[])
    return [s.action for s in events]


def _trend_closes(drift: float, n: int = 120, seed: int = 3) -> list[float]:
    rng = random.Random(seed)
    px, out = 100.0, []
    for _ in range(40):
        px *= 1.0 + rng.uniform(-0.0002, 0.0002)
        out.append(px)
    for _ in range(n):
        px *= 1.0 + drift + rng.uniform(-0.0004, 0.0004)
        out.append(px)
    return out


def test_consensus_cost_floor_blocks_small_moves():
    """The cost floor is amortized over the 20-bar regime window: k*C = 52 bps
    spread over 20 bars is ~2.6 bps/bar, so a ~1 bp/bar regime (which would
    accumulate only ~20 bps of total movement) must be blocked."""
    cm = CostModel(flat_fee_bps=10.0, flat_slippage_bps=3.0)  # C=26bps, k*C=52bps
    rng = random.Random(3)
    px, closes = 100.0, []
    for _ in range(40):  # flat base so the EMAs seed without a stale gap
        px *= 1.0 + rng.uniform(-0.00005, 0.00005)
        closes.append(px)
    for _ in range(120):  # ~1 bp/bar: below the amortized floor
        px *= 1.0 + 0.0001 + rng.uniform(-0.00005, 0.00005)
        closes.append(px)
    # move_floor=0 isolates the cost gate from the plain regime filter.
    plain = ConsensusStrategy(symbols=("SPY",), move_floor=0.0)
    gated = ConsensusStrategy(symbols=("SPY",), move_floor=0.0, costs=cm)
    assert SignalAction.ENTER_LONG in _feed(plain, "SPY", closes)
    assert _feed(gated, "SPY", closes) == []


def test_consensus_cost_gate_passes_strong_moves():
    cm = CostModel(flat_fee_bps=10.0, flat_slippage_bps=3.0)
    closes = _trend_closes(drift=0.010)  # ~1%/bar >> 52 bps floor
    gated = ConsensusStrategy(symbols=("SPY",), costs=cm)
    actions = _feed(gated, "SPY", closes)
    assert SignalAction.ENTER_LONG in actions


def test_consensus_exit_band_tightens_with_costs():
    cm = CostModel(flat_fee_bps=10.0, flat_slippage_bps=3.0)
    gated = ConsensusStrategy(symbols=("SPY",), costs=cm, threshold=0.5)
    plain = ConsensusStrategy(symbols=("SPY",), threshold=0.5)
    votes = Votes(ema=1, macd=1, rsi=1, bollinger=1)
    gated._last_move_rms["SPY"] = 0.01
    # k*C = 52 bps; buffer = min(0.15, 0.0052/(0.798*0.01)) = 0.15 ->
    # band 0.25 - 0.15 = 0.10: the gated position must retrace deeper (0.15)
    # before it exits, so it is not churned at breakeven noise.
    assert not gated._should_exit(0.15, votes, 1, "SPY")
    assert plain._should_exit(0.15, votes, 1)
    assert gated._should_exit(0.05, votes, 1, "SPY")


# ---- momentum scalper gate -------------------------------------------------


def test_momentum_cost_floor():
    from entropy.bot.strategies.momentum_scalper import MomentumScalper
    from entropy.engine.events import Spike

    cm = CostModel(flat_fee_bps=10.0, flat_slippage_bps=3.0)  # floor 0.52%
    plain = MomentumScalper(symbols=("SPY",), min_pct=0.5)
    gated = MomentumScalper(symbols=("SPY",), min_pct=0.5, costs=cm)
    spike = Spike(symbol="SPY", ts_ns=1, price=100.0, pct=0.5, horizon_s=5.0,
                  ref_price=100.0)
    ts = 1_700_000_000_000_000_000
    assert [s.action for s in plain.on_tick("SPY", 100.0, ts, [spike])] == \
        [SignalAction.ENTER_LONG]
    assert gated.on_tick("SPY", 100.0, ts, [spike]) == []
    big = Spike(symbol="SPY", ts_ns=1, price=100.0, pct=0.6, horizon_s=5.0,
                ref_price=100.0)
    assert [s.action for s in gated.on_tick("SPY", 100.0, ts, [big])] == \
        [SignalAction.ENTER_LONG]


# ---- calibration -----------------------------------------------------------


def test_run_backtest_market_costs_metric():
    ticks = generate_ticks(["SPY", "SOLUSDT"], 1500, seed=42)
    flat = run_backtest(ticks, ["SPY", "SOLUSDT"], fast=9, slow=21, min_pct=0.15,
                        stop_loss_pct=1.0, take_profit_pct=2.0)
    # market_costs=None is the legacy flat path with every gate off, so an
    # explicit cost_aware=False must produce the identical run.
    forced_flat = run_backtest(ticks, ["SPY", "SOLUSDT"], fast=9, slow=21, min_pct=0.15,
                               stop_loss_pct=1.0, take_profit_pct=2.0,
                               market_costs=None, cost_aware=False)
    assert forced_flat == flat
    assert flat["costs_paid"] >= 0.0
    costed = run_backtest(ticks, ["SPY", "SOLUSDT"], fast=9, slow=21, min_pct=0.15,
                          stop_loss_pct=1.0, take_profit_pct=2.0,
                          market_costs=MarketCostConfig())
    assert costed["costs_paid"] >= 0.0
    # Same explicit config twice -> deterministic.
    again = run_backtest(ticks, ["SPY", "SOLUSDT"], fast=9, slow=21, min_pct=0.15,
                         stop_loss_pct=1.0, take_profit_pct=2.0,
                         market_costs=MarketCostConfig())
    assert again == costed


# ---- config wiring ---------------------------------------------------------


def test_cost_aware_false_restores_legacy_wiring():
    from entropy.bot.config import BotConfig

    legacy = BotConfig(cost_aware=False, market_costs=MarketCostConfig.flat())
    assert legacy.cost_model() is None  # every gate is a no-op, flat fees only
    default = BotConfig()
    cm = default.cost_model()
    assert cm is not None
    assert cm.round_trip_bps("SPY") == pytest.approx(8.0)  # equity 2.0/2.0
    assert cm.round_trip_bps("SOLUSDT") == pytest.approx(26.0)  # spot 10.0/3.0
