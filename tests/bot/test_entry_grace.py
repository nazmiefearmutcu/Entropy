"""T6 entry-bar grace + T7 risk-trail through the accuracy harness
(scripts/entropy_accuracy_btc15m.py).

T6 pins the ``--entry-grace-bars`` semantics on deterministic synthetic
klines: same-bar stops suppressed, the grace counting from the ENTRY bar
(entry bar + N-1 following bars), TP staying live during grace, grace=0
byte-identical, barrier values unchanged (the stop exit after grace clamps to
the ANCHORED level). T7 pins the ratcheted-stop level-fill capture: a
breakeven ratchet exit clamps to the RATCHETED stop, not the deep anchored
one. The script is imported via importlib (same pattern as
tests/bot/test_accuracy_harness.py).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "entropy_accuracy_btc15m.py"
_spec = importlib.util.spec_from_file_location("entropy_accuracy_btc15m", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)

from entropy.bot.config import (  # noqa: E402
    BotConfig,
    ConsensusConfig,
    MarketCostConfig,
    RiskOverrides,
)

SYMBOL = mod.SYMBOL
BAR_MS = mod.BAR_MS
T0_MS = 1_700_000_000_000  # fixed epoch so timestamps are deterministic
SLIP = 0.0003              # binance-spot slippage, 3 bps


def _kline(i: int, o: float, c: float, hi: float | None = None,
           lo: float | None = None) -> list[Any]:
    hi = max(o, c) * 1.002 if hi is None else hi
    lo = min(o, c) * 0.998 if lo is None else lo
    return [T0_MS + i * BAR_MS, o, hi, lo, c, 1.0,
            T0_MS + i * BAR_MS + BAR_MS - 1, 0, 0, 0, 0, 0]


def _ramp(n: int, start: float = 100.0, pct: float = 1.003) -> tuple[list[list[Any]], float]:
    klines: list[list[Any]] = []
    px = start
    for i in range(n):
        o, c = px, px * pct
        klines.append(_kline(i, o, c))
        px = c
    return klines, px


def _cfg(fee_bps: float = 10.0, slip_bps: float = 3.0,
         sl_pct: float = 1.5, tp_pct: float = 1.2,
         risk_trail_pct: float = 0.0,
         max_hold_bars: int = 0) -> BotConfig:
    # Same legacy no-op shape as test_accuracy_harness._cfg (see its docstring):
    # percent barriers, no direction filter, no time stop unless asked.
    return BotConfig(
        mode="paper", starting_cash=100.0, strategies=("consensus",),
        symbols=(SYMBOL,), ema_symbol=SYMBOL, ema_fast=9, ema_slow=21,
        momentum_min_pct=0.15, timeframe="15m", bar_s=900.0, warmup=False,
        market_costs=MarketCostConfig(
            crypto_spot_fee_bps=fee_bps, crypto_spot_slippage_bps=slip_bps,
        ),
        cost_aware=True, cost_edge_mult=1.0,
        consensus=ConsensusConfig(
            exit_mode="hold", min_hold_bars=0, move_floor=0.0003,
            confirm_bars=2, cooldown_bars=2,
            direction_bars=0, max_hold_bars=max_hold_bars, long_only=False,
        ),
        risk_overrides=RiskOverrides(
            stop_loss_pct=sl_pct, take_profit_pct=tp_pct, vol_window_s=30.0,
            stop_mode="percent", stop_sigma_mult=1.5, tp_sigma_mult=1.2,
            risk_trail_pct=risk_trail_pct,
        ),
        console_log_path="/tmp/entropy_harness_test/console.log",
        trade_csv_path="/tmp/entropy_harness_test/trades.csv",
    )


def _run(klines: list[list[Any]], cfg: BotConfig, **kw: Any) -> dict[str, Any]:
    return mod.simulate(klines, cfg, run_dir="/tmp/entropy_harness_test/ledger", **kw)


def _bar_of(iso: str) -> int:
    ms = datetime.fromisoformat(iso).timestamp() * 1000.0
    return round((ms - T0_MS) / BAR_MS)


# ---- T6: entry-bar grace ----------------------------------------------------------


def _entry_bar_deep_low(n_after: int = 12) -> list[list[Any]]:
    """Ramp where the entry (bar 36's O tick) is followed by a bar whose LOW
    sits deep below the anchored stop (0.985 * entry_fill); then a ramp that
    would carry the position to its TP. Bars 0..35 qualify the entry at the
    roll into bar 36."""
    klines, px = _ramp(36)
    klines.append(_kline(36, px, px * 1.003, hi=px * 1.006, lo=px * 0.97))
    for i in range(37, 36 + n_after):
        o, c = klines[-1][4], klines[-1][4] * 1.003
        klines.append(_kline(i, o, c))
    return klines


def test_same_bar_stop_suppressed_when_grace_ge_1():
    """grace=0 stops on the entry bar's low tick; grace=1 lets the position
    ride and the take-profit closes it later (a win instead of a same-bar
    stop)."""
    klines = _entry_bar_deep_low()
    r0 = _run(klines, _cfg())
    r1 = _run(klines, _cfg(), entry_grace_bars=1)
    t0, t1 = r0["trades"][0], r1["trades"][0]
    assert t0["exit_intent"] == "stop"
    assert _bar_of(t0["exit_ts"]) == 36            # the entry bar itself
    assert t0["pnl"] < 0
    assert t1["exit_intent"] == "take_profit"
    assert _bar_of(t1["exit_ts"]) == 39            # the TP fires later
    assert t1["pnl"] > 0


def test_grace_bars_count_from_the_entry_bar():
    """Counting: the entry bar (bar where the O tick opened) is bar 0 of the
    grace, so grace=N exempts the entry bar + N-1 following bars. With a
    stop-crossing low on each of bars 36..38: grace=0 stops at 36, grace=1 at
    37, grace=2 at 38."""
    klines, px = _ramp(36)
    for i in range(36, 39):
        o = klines[-1][4]
        klines.append(_kline(i, o, o * 1.002, hi=o * 1.004, lo=o * 0.97))
    for grace, expected_bar in ((0, 36), (1, 37), (2, 38)):
        report = _run(klines, _cfg(), entry_grace_bars=grace)
        trades = report["trades"]
        assert len(trades) == 1, report["trades"]
        assert trades[0]["exit_intent"] == "stop"
        assert _bar_of(trades[0]["exit_ts"]) == expected_bar, (
            f"grace={grace} must stop at bar {expected_bar}")


def test_take_profit_still_live_during_grace():
    """A bar whose range contains BOTH the stop and the TP: grace=0 resolves
    the stop first (pessimistic); grace>=1 suppresses the stop and the TP
    closes the position INSIDE the entry bar, clamped to the TP level."""
    klines, px = _ramp(36)
    klines.append(_kline(36, px, px * 1.001, hi=px * 1.015, lo=px * 0.97))
    r0 = _run(klines, _cfg())
    r1 = _run(klines, _cfg(), entry_grace_bars=1)
    t0, t1 = r0["trades"][0], r1["trades"][0]
    assert t0["exit_intent"] == "stop"
    assert t1["exit_intent"] == "take_profit"
    assert _bar_of(t1["exit_ts"]) == 36           # TP still live in the entry bar
    assert t1["pnl"] > 0
    assert t1["exit_px"] == pytest.approx(
        t1["entry_px"] * (1 + 0.012) * (1 - SLIP), rel=1e-3)


def test_grace_zero_is_byte_identical():
    """grace=0 (the default) must be byte-identical to the current behavior,
    and the report carries the knob."""
    klines = _entry_bar_deep_low()
    r = _run(klines, _cfg())
    r0 = _run(klines, _cfg(), entry_grace_bars=0)
    assert r0 == r
    assert r["entry_grace_bars"] == 0


def test_grace_does_not_reanchor_barriers():
    """During grace the stop is only paused, never moved: a stop that fires
    AFTER grace (bar 37's low sitting exactly ON the anchored level) clamps to
    the ANCHORED barrier, proving the barrier values never changed."""
    klines, px = _ramp(36)
    klines.append(_kline(36, px, px * 1.003, hi=px * 1.005, lo=px * 0.985))
    o37 = klines[-1][4]
    anchored_stop = px * (1 + SLIP) * 0.985       # entry fill * (1 - 1.5%)
    klines.append(_kline(37, o37, o37 * 1.003, hi=o37 * 1.004, lo=anchored_stop))
    report = _run(klines, _cfg(), entry_grace_bars=1)
    trades = report["trades"]
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_intent"] == "stop"
    assert _bar_of(t["exit_ts"]) == 37            # survived the entry bar
    assert t["exit_px"] == pytest.approx(anchored_stop * (1 - SLIP), rel=1e-4)


def test_grace_leaves_strategy_exits_untouched():
    """The grace suppresses only the MECHANICAL stop: a strategy time-stop
    (max_hold_bars=1) fires during the grace window with intent 'close'."""
    klines, px = _ramp(36)
    klines.append(_kline(36, px, px * 1.001, hi=px * 1.001, lo=px * 1.001))
    for i in range(37, 44):                       # flat bars: no stop/TP hits
        klines.append(_kline(i, px * 1.001, px * 1.001, hi=px * 1.001, lo=px * 1.001))
    report = _run(klines, _cfg(max_hold_bars=1), entry_grace_bars=5)
    trades = report["trades"]
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_intent"] == "close"            # the time stop, not a stop
    assert _bar_of(t["exit_ts"]) == 37            # inside the grace window


def test_grace_exempt_helper_counts_bars_from_entry():
    bar_ns = int(900 * 1e9)
    entry = 1_700_000_000_000_000_000
    assert not mod._grace_exempt(entry, entry, bar_ns, 0)              # off
    assert mod._grace_exempt(entry, entry, bar_ns, 1)                  # entry-bar O tick
    assert mod._grace_exempt(entry, entry + 3 * 1_000_000_000, bar_ns, 1)  # L/H/C ticks
    assert not mod._grace_exempt(entry, entry + bar_ns, bar_ns, 1)     # next bar: live at N=1
    assert mod._grace_exempt(entry, entry + bar_ns, bar_ns, 2)         # ...exempt at N=2
    assert mod._grace_exempt(entry, entry + 2 * bar_ns, bar_ns, 3)
    assert not mod._grace_exempt(entry, entry + 2 * bar_ns, bar_ns, 2)
    assert not mod._grace_exempt(entry, entry + 100 * bar_ns, bar_ns, 3)


# ---- T7: risk-trail through the harness -------------------------------------------

def test_risk_trail_breakeven_exit_in_harness():
    """T7 integration: r=0.5. The entry bar's HIGH crosses the trigger (entry +
    0.5*tp_dist) -> the stop ratchets to entry; the next bar's low dips below
    entry -> the position stops at ~breakeven, and the level-fill clamp uses
    the RATCHETED stop (the fill is NOT re-priced at the deep anchored 1.5%
    stop). Without the trail the same bars run to the take-profit."""
    klines, px = _ramp(36)
    klines.append(_kline(36, px, px * 1.003, hi=px * 1.009, lo=px * 0.99))
    o37 = klines[-1][4]
    klines.append(_kline(37, o37, o37 * 1.003, hi=o37 * 1.004, lo=o37 * 0.995))
    for i in range(38, 50):
        o, c = klines[-1][4], klines[-1][4] * 1.003
        klines.append(_kline(i, o, c))
    report = _run(klines, _cfg(risk_trail_pct=0.5))
    t = report["trades"][0]
    assert t["exit_intent"] == "stop"
    assert _bar_of(t["exit_ts"]) == 37
    # the exit fill is the bar-37 low tick (the ratcheted breakeven stop fired
    # there): far above the anchored-stop clamp of a no-trail stop
    assert t["exit_px"] == pytest.approx(o37 * 0.995 * (1 - SLIP), rel=1e-3)
    assert t["exit_px"] > px * (1 + SLIP) * 0.985 * (1 - SLIP)
    assert t["pnl"] < 0
    # same bars, trail off: no ratchet, the position runs to the take-profit
    r_off = _run(klines, _cfg())
    assert r_off["trades"][0]["exit_intent"] == "take_profit"