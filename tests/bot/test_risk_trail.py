"""T7 risk-trail stop ratchet (Round 2): breakeven / profit-slice stop.

RiskManager unit tests for the per-tick monotonic ratchet (runs BEFORE stop/TP
hit-checking), RiskOverrides validation + active() lossiness, the runner
plumbing and the hot-apply path. All mechanisms default OFF (0.0).
"""

from __future__ import annotations

import pytest

from entropy.bot.config import BotConfig, RiskOverrides, validate, warnings
from entropy.bot.orders import OrderIntent
from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import MEDIUM
from entropy.bot.runner import BotRunner


def _rm(pct: float = 0.0) -> RiskManager:
    return RiskManager(MEDIUM, risk_trail_pct=pct)


def _open_long(p: Portfolio, entry: float = 100.0, stop: float = 95.0,
               tp: float = 104.0) -> None:
    p.open("SPY", PositionSide.LONG, 10.0, entry, stop, tp, ts_ns=1, fee=0.0)


def _tick(rm: RiskManager, p: Portfolio, px: float, ts_ns: int) -> list:
    p.mark("SPY", px)
    return rm.check_exits(p, ts_ns)


# ---- the ratchet itself ----------------------------------------------------------


def test_ratchet_to_breakeven_at_0_5():
    """0.5 of the TP distance (entry 100, tp 104 -> trigger 102): once the mark
    crosses the trigger the stop ratchets to the ENTRY (breakeven)."""
    rm = _rm(0.5)
    p = Portfolio(100_000.0)
    _open_long(p)
    orders = _tick(rm, p, 102.5, ts_ns=2)
    assert orders == []                      # the trigger tick must not stop it
    assert p.positions["SPY"].stop_px == 100.0
    # idempotent: another tick past the trigger keeps the same stop
    _tick(rm, p, 103.0, ts_ns=3)
    assert p.positions["SPY"].stop_px == 100.0


def test_ratchet_never_loosens():
    """Monotonic: the stop only ever moves TOWARD the TP (long: only rises)."""
    rm = _rm(0.5)
    p = Portfolio(100_000.0)
    _open_long(p)
    _tick(rm, p, 103.0, ts_ns=2)             # ratchet -> stop 100
    assert p.positions["SPY"].stop_px == 100.0
    _tick(rm, p, 101.0, ts_ns=3)             # below the trigger, above the stop
    assert p.positions["SPY"].stop_px == 100.0
    _tick(rm, p, 96.0, ts_ns=4)              # still above the anchored 95
    assert p.positions["SPY"].stop_px == 100.0
    # a crash below the ratcheted stop now stops at ~entry, not the deep 95
    orders = _tick(rm, p, 94.0, ts_ns=5)
    assert [o.intent for o in orders] == [OrderIntent.STOP]


def test_ratchet_keeps_profit_slice_at_1_5():
    """>= 1.0 keeps a profit slice: 1.5 -> stop 50% of the way to the TP."""
    rm = _rm(1.5)
    p = Portfolio(100_000.0)
    _open_long(p, tp=108.0)                  # tp_dist 8 -> trigger 112, target 104
    orders = _tick(rm, p, 112.0, ts_ns=2)
    assert p.positions["SPY"].stop_px == 104.0
    # the same tick also crosses the LIVE take-profit -> TP closes the position
    # (the ratchet ran first; the exit level captured by the harness would be 104)
    assert [o.intent for o in orders] == [OrderIntent.TAKE_PROFIT]


def test_ratchet_at_1_0_moves_stop_to_entry():
    rm = _rm(1.0)
    p = Portfolio(100_000.0)
    _open_long(p)                            # tp_dist 4 -> trigger 104 (= tp), target 100
    _tick(rm, p, 104.0, ts_ns=2)
    assert p.positions["SPY"].stop_px == 100.0


def test_ratchet_short_side_mirrors():
    """Short mirror: stop ABOVE entry; the trigger sits BELOW entry; the
    ratcheted stop only falls (toward the TP)."""
    rm = _rm(0.5)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.SHORT, 10.0, 100.0, stop_px=105.0, tp_px=96.0,
           ts_ns=1, fee=0.0)                 # tp_dist 4 -> trigger 98, target 100
    orders = _tick(rm, p, 97.5, ts_ns=2)
    assert orders == []
    assert p.positions["SPY"].stop_px == 100.0
    orders = _tick(rm, p, 100.5, ts_ns=3)    # crossed the NEW (breakeven) stop
    assert [o.intent for o in orders] == [OrderIntent.STOP]


def test_ratchet_short_side_profit_slice():
    rm = _rm(1.5)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.SHORT, 10.0, 100.0, stop_px=105.0, tp_px=96.0,
           ts_ns=1, fee=0.0)                 # tp_dist 4 -> trigger 94, target 98
    orders = _tick(rm, p, 93.5, ts_ns=2)
    assert p.positions["SPY"].stop_px == 98.0
    assert [o.intent for o in orders] == [OrderIntent.TAKE_PROFIT]


def test_ratchet_off_at_zero_keeps_anchored_stop():
    """0.0 = off: the anchored stop never moves, however deep into profit the
    mark runs (existing behavior, byte-identical)."""
    rm = _rm(0.0)
    p = Portfolio(100_000.0)
    _open_long(p)
    _tick(rm, p, 103.5, ts_ns=2)
    assert p.positions["SPY"].stop_px == 95.0
    orders = _tick(rm, p, 94.9, ts_ns=3)
    assert [o.intent for o in orders] == [OrderIntent.STOP]


# ---- ratchet-before-hit ordering --------------------------------------------------


def test_ratchet_runs_before_stop_hit_check():
    """The stop level a tick sees is the level AFTER any ratchet that tick
    triggered. Long r=0.5 (old stop 95, trigger 102, new stop 100):
    * the 102.5 tick triggers the ratchet and must NOT stop the position;
    * the 99.5 tick is ABOVE the old stop 95 yet BELOW the new stop 100 — it
      stops ONLY because the ratchet ran first (a post-check ratchet would have
      let it ride at 99.5, the old stop never tripping).
    """
    rm = _rm(0.5)
    p = Portfolio(100_000.0)
    _open_long(p)
    orders = _tick(rm, p, 102.5, ts_ns=2)    # ratchet fires; survives
    assert orders == []
    assert p.positions["SPY"].stop_px == 100.0
    orders = _tick(rm, p, 99.5, ts_ns=3)     # crosses the NEW stop, not the old
    assert [o.intent for o in orders] == [OrderIntent.STOP]


def test_ratchet_does_not_change_tp_hit_behavior():
    """A tick past the trigger that ALSO crosses the TP still closes via the
    TP (the ratchet never delays the take-profit)."""
    rm = _rm(0.5)
    p = Portfolio(100_000.0)
    _open_long(p)
    orders = _tick(rm, p, 104.5, ts_ns=2)    # >= trigger 102 AND >= tp 104
    assert [o.intent for o in orders] == [OrderIntent.TAKE_PROFIT]
    assert p.positions["SPY"].stop_px == 100.0


# ---- config validation + plumbing -------------------------------------------------


def test_risk_overrides_validate_risk_trail_pct():
    with pytest.raises(ValueError):
        RiskOverrides(risk_trail_pct=-0.01)
    assert RiskOverrides().risk_trail_pct == 0.0      # default off
    assert RiskOverrides(risk_trail_pct=0.5).risk_trail_pct == 0.5


def test_validate_reports_negative_risk_trail_pct():
    # construction already refuses negatives (ValueError above); validate() is
    # the defensive gate for structs mutated past __post_init__
    from msgspec.structs import force_setattr

    ro = RiskOverrides()
    force_setattr(ro, "risk_trail_pct", -1.0)
    problems = validate(BotConfig(risk_overrides=ro))
    assert any("risk_trail_pct" in p for p in problems)
    assert validate(BotConfig()) == []


def test_risk_trail_pct_never_handed_to_make_custom():
    """active() is lossy for risk_trail_pct (it is not a RiskProfile field):
    cfg.profile() must keep working with the trail enabled."""
    ro = RiskOverrides(risk_trail_pct=0.5)
    assert "risk_trail_pct" not in ro.active()
    cfg = BotConfig(risk_overrides=ro)
    assert cfg.profile().name == MEDIUM.name


def test_warnings_note_trail_with_percent_mode():
    """A trail fraction with percent barriers still works (it is a fraction of
    the percent TP distance) — a warning, not an error."""
    notes = warnings(BotConfig(risk_overrides=RiskOverrides(
        risk_trail_pct=0.5, stop_mode="percent")))
    assert any("risk_trail_pct" in n and "percent" in n for n in notes)
    # sigma mode (the shipped default) and trail-off stay silent
    assert warnings(BotConfig(risk_overrides=RiskOverrides(risk_trail_pct=0.5))) == []
    assert warnings(BotConfig()) == []


def test_runner_plumbs_risk_trail_pct_and_hot_applies(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False,
                    risk_overrides=RiskOverrides(risk_trail_pct=0.5))
    runner = BotRunner(cfg, run_dir=str(tmp_path))
    assert runner.risk.risk_trail_pct == 0.5
    cfg2 = BotConfig(enable_crypto=False, enable_equities=False,
                     risk_overrides=RiskOverrides(risk_trail_pct=1.5))
    assert runner.apply_config(cfg2) == []
    assert runner.risk.risk_trail_pct == 1.5