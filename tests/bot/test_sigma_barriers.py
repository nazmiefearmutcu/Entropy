"""Sigma-scaled stop/TP barriers (T3): RiskManager override path, runner
plumbing from the entry signal's ``sigma`` to the anchored barriers, and the
RiskOverrides validation. Percent mode must stay bit-for-bit legacy."""

from __future__ import annotations

import pytest

from entropy.bot.config import BotConfig, ConsensusConfig, RiskOverrides, validate
from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import MEDIUM
from entropy.bot.runner import BotRunner
from entropy.bot.signals import Signal, SignalAction

_NS = 1_000_000_000


def _volatile_history(rm: RiskManager, symbol: str = "SPY") -> None:
    """6 in-window ticks that WOULD scale the percent barriers by 22/21 in
    legacy mode — the sigma override must skip that scale factor entirely."""
    for i, px in enumerate((100.0, 110.0, 100.0, 110.0, 100.0, 110.0)):
        rm.update_tick(symbol, px, 1000 + i)


def test_stop_tp_prices_sigma_override_ignores_window_scale():
    rm = RiskManager(MEDIUM)
    _volatile_history(rm)
    # sigma = 0.0011 with the default multipliers (1.5 / 1.2) -> 0.165% / 0.132%
    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0, "SPY",
                               stop_pct=0.165, tp_pct=0.132)
    assert sl == pytest.approx(100.0 * (1 - 0.165 / 100))
    assert tp == pytest.approx(100.0 * (1 + 0.132 / 100))
    sl_s, tp_s = rm.stop_tp_prices(PositionSide.SHORT, 100.0, "SPY",
                                   stop_pct=0.165, tp_pct=0.132)
    assert sl_s == pytest.approx(100.0 * (1 + 0.165 / 100))
    assert tp_s == pytest.approx(100.0 * (1 - 0.132 / 100))


def test_stop_tp_prices_sigma_override_is_clamped_at_50pct():
    rm = RiskManager(MEDIUM)
    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0, "SPY",
                               stop_pct=80.0, tp_pct=60.0)
    assert sl == pytest.approx(100.0 * (1 - 50.0 / 100))
    assert tp == pytest.approx(100.0 * (1 + 50.0 / 100))


def test_stop_tp_prices_without_override_is_legacy_behavior():
    """No overrides: the tick-window volatility scale factor applies exactly as
    before (22/21 with the alternating history)."""
    rm = RiskManager(MEDIUM)
    _volatile_history(rm)
    scale = 22.0 / 21.0
    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
    assert sl == pytest.approx(100.0 * (1 - (MEDIUM.stop_loss_pct * scale) / 100))
    assert tp == pytest.approx(100.0 * (1 + (MEDIUM.take_profit_pct * scale) / 100))


def test_risk_overrides_barriers_validate_at_construction():
    with pytest.raises(ValueError):
        RiskOverrides(stop_mode="atr")
    with pytest.raises(ValueError):
        RiskOverrides(stop_sigma_mult=0.0)
    with pytest.raises(ValueError):
        RiskOverrides(tp_sigma_mult=-1.0)
    # defaults are the no-op percent shape
    ro = RiskOverrides()
    assert ro.stop_mode == "percent"
    assert (ro.stop_sigma_mult, ro.tp_sigma_mult) == (1.5, 1.2)
    # barrier fields are never handed to make_custom (they are not profile
    # fields) — cfg.profile() must keep working with sigma mode on
    cfg = BotConfig(risk_overrides=RiskOverrides(stop_mode="sigma"))
    assert cfg.profile().name == "Medium"


def test_validate_reports_time_stop_problems():
    """max_hold_bars < min_hold_bars (and not 0) can never fire — validate()
    must name it. (Bad stop_mode / non-positive sigma multipliers are refused
    earlier, by RiskOverrides.__post_init__ raising ValueError.)"""
    bad = BotConfig(consensus=ConsensusConfig(min_hold_bars=5, max_hold_bars=2))
    problems = validate(bad)
    assert any("max_hold_bars" in p for p in problems)
    assert validate(BotConfig()) == []
    # a compatible time stop validates clean
    ok = BotConfig(consensus=ConsensusConfig(min_hold_bars=5, max_hold_bars=5))
    assert validate(ok) == []


class _SigmaStub:
    """Emits one sigma-carrying entry signal per tick, then goes silent."""

    name = "sigma_stub"

    def __init__(self, sigma: float | None) -> None:
        self._sigma = sigma
        self._fired = False

    def on_tick(self, symbol, price, ts_ns, events):
        if self._fired:
            return []
        self._fired = True
        return [Signal(symbol=symbol, action=SignalAction.ENTER_LONG, strength=1.0,
                       reason="stub", ts_ns=ts_ns, strategy=self.name,
                       sigma=self._sigma)]


def _run_one_entry(tmp_path, stop_mode: str, sigma: float | None):
    cfg = BotConfig(
        starting_cash=100_000.0, enable_crypto=False, enable_equities=False,
        risk_overrides=RiskOverrides(stop_mode=stop_mode),
    )
    runner = BotRunner(cfg, run_dir=str(tmp_path))
    runner.strategies = [_SigmaStub(sigma)]
    runner.on_trade("SPY", 100.0, 1.0, "buy", 1_000)
    pos = runner.portfolio.positions.get("SPY")
    assert pos is not None, "stub entry must fill"
    return runner, pos


# equity costs resolve to 2 bps fee / 2 bps slippage -> BUY fill = 100.02
_FILL = 100.0 * (1 + 2.0 / 10_000.0)


def test_runner_sigma_mode_anchors_barriers_from_entry_sigma(tmp_path):
    # sigma = 0.002 clears the sigma-mode cost gate (stop 0.3% -> cost-to-stop
    # 0.08/0.3 = 0.27 <= 0.5, tp 24 bps > 8 bps RT) and anchors at the multipliers
    runner, pos = _run_one_entry(tmp_path, "sigma", sigma=0.002)
    # stop = 1.5 * 0.002 = 0.3% below the fill, tp = 1.2 * 0.002 = 0.24% above
    assert pos.stop_px == pytest.approx(_FILL * (1 - 0.003))
    assert pos.tp_px == pytest.approx(_FILL * (1 + 0.0024))
    # the stash is consumed: nothing left behind for a later unrelated entry
    assert runner._entry_sigma == {}


def test_runner_sigma_mode_rejects_are_dropped(tmp_path):
    """An entry the risk layer refuses must not leave a stale sigma behind."""
    cfg = BotConfig(
        enable_crypto=False, enable_equities=False,
        risk_overrides=RiskOverrides(stop_mode="sigma", per_trade_pct=0.0),  # qty <= 0
    )
    runner = BotRunner(cfg, run_dir=str(tmp_path))
    runner.strategies = [_SigmaStub(sigma=0.001)]
    runner.on_trade("SPY", 100.0, 1.0, "buy", 1_000)
    assert "SPY" not in runner.portfolio.positions
    assert runner._entry_sigma == {}


def test_runner_missing_sigma_falls_back_to_percent(tmp_path):
    runner, pos = _run_one_entry(tmp_path, "sigma", sigma=None)
    # 1 tick of history (< 5) -> no volatility scaling: MEDIUM 1% / 2%
    assert pos.stop_px == pytest.approx(_FILL * (1 - MEDIUM.stop_loss_pct / 100))
    assert pos.tp_px == pytest.approx(_FILL * (1 + MEDIUM.take_profit_pct / 100))


def test_runner_percent_mode_ignores_entry_sigma(tmp_path):
    runner, pos = _run_one_entry(tmp_path, "percent", sigma=0.001)
    assert pos.stop_px == pytest.approx(_FILL * (1 - MEDIUM.stop_loss_pct / 100))
    assert pos.tp_px == pytest.approx(_FILL * (1 + MEDIUM.take_profit_pct / 100))


# ---- round 1: the entry cost gate must judge the sigma barriers -------------


def _gate_manager(stop_mode: str) -> RiskManager:
    from entropy.bot.costs import CostModel

    return RiskManager(
        MEDIUM,
        cost_model=CostModel(flat_fee_bps=10.0, flat_slippage_bps=3.0),  # 26 bps RT
        stop_mode=stop_mode,
    )


def test_barrier_pcts_helper():
    rm = _gate_manager("sigma")
    assert rm.barrier_pcts(0.0011) == pytest.approx((0.165, 0.132))
    assert rm.barrier_pcts(None) is None
    assert rm.barrier_pcts(0.0) is None
    pct_rm = _gate_manager("percent")
    assert pct_rm.barrier_pcts(0.0011) is None


def test_sigma_mode_cost_gate_rejects_cost_dead_entries():
    """sigma ~ 0.0011 (BTC-15m-like) anchors a ~13 bps TP — below the 26 bps
    round trip. Under percent-barrier judging (2% TP = 200 bps) this entry was
    approved despite being guaranteed cost-dead; the gate must now judge the
    sigma barriers and reject."""
    rm = _gate_manager("sigma")
    p = Portfolio(100_000.0)
    sig = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=1.0,
                 reason="t", ts_ns=1, strategy="s", sigma=0.0011)
    d = rm.evaluate(sig, p, mark_px=100.0, ts_ns=1)
    assert not d.approved
    assert d.reason == "take-profit below round-trip cost"


def test_sigma_mode_cost_gate_still_allows_viable_sigma():
    """sigma = 0.005 -> TP = 0.6% = 60 bps > 26 bps RT and cost-to-stop
    26/75 = 0.35 <= 0.5: the sigma-mode gate must approve."""
    rm = _gate_manager("sigma")
    p = Portfolio(100_000.0)
    sig = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=1.0,
                 reason="t", ts_ns=1, strategy="s", sigma=0.005)
    d = rm.evaluate(sig, p, mark_px=100.0, ts_ns=1)
    assert d.approved


def test_percent_mode_cost_gate_ignores_sigma():
    """Percent mode (the default) gates on the profile's percents exactly as
    before, whatever sigma the signal carries."""
    rm = _gate_manager("percent")
    p = Portfolio(100_000.0)
    sig = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=1.0,
                 reason="t", ts_ns=1, strategy="s", sigma=0.0011)
    assert rm.evaluate(sig, p, mark_px=100.0, ts_ns=1).approved


def test_sigma_mode_missing_sigma_gates_on_percent_fallback():
    """No usable sigma on the signal: the gate (like the open path) falls back
    to the profile's percent barriers."""
    rm = _gate_manager("sigma")
    p = Portfolio(100_000.0)
    sig = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=1.0,
                 reason="t", ts_ns=1, strategy="s", sigma=None)
    assert rm.evaluate(sig, p, mark_px=100.0, ts_ns=1).approved


def test_runner_plumbs_barrier_mode_into_risk_and_hot_apply(tmp_path):
    cfg = BotConfig(
        enable_crypto=False, enable_equities=False,
        risk_overrides=RiskOverrides(stop_mode="sigma", stop_sigma_mult=2.0,
                                     tp_sigma_mult=1.0),
    )
    runner = BotRunner(cfg, run_dir=str(tmp_path))
    assert runner.risk.stop_mode == "sigma"
    assert (runner.risk.stop_sigma_mult, runner.risk.tp_sigma_mult) == (2.0, 1.0)
    # hot-apply a percent config: the risk layer's gate must follow
    cfg2 = BotConfig(
        enable_crypto=False, enable_equities=False,
        risk_overrides=RiskOverrides(),
    )
    assert runner.apply_config(cfg2) == []
    assert runner.risk.stop_mode == "percent"
    assert runner.risk.barrier_pcts(0.0011) is None
