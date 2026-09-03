"""Honest-measurement integrity of the accuracy harness
(scripts/entropy_accuracy_btc15m.py).

The harness feeds kline-shaped ticks through the final-state BotRunner, so
these tests pin the measurement conventions on deterministic synthetic klines
(a clean 0.3%/bar ramp enters longs reliably; the first entry always fires at
the roll into bar 36, i.e. confirm_bars=2 both qualifying bars; risk barriers
are fixed percentages of the slippage-adjusted entry fill because
vol_window_s=30 leaves fewer than 5 window ticks at entry):

* pessimistic intrabar barrier resolution — the low/high tick order inside a
  bar is DIRECTION-AWARE (an open long feeds L before H, an open short feeds
  H before L; the order is chosen after the open tick, so just-opened
  positions are covered): when one bar's range contains both an open
  position's stop and its take-profit the STOP resolves first, for either
  direction;
* level fills — mechanical stop/TP exit fills are clamped, when pairing, to
  the barrier level anchored at entry (worse of level and actual fill, both
  with the close-side adverse slippage), never left at the bar extreme;
* zero PnL is a loss;
* long-only reporting;
* warmup chaining (--warmup-bars).

The script lives in scripts/ (not a package) and is imported via importlib.
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
    """n up-bars; returns (klines, last close)."""
    klines: list[list[Any]] = []
    px = start
    for i in range(n):
        o, c = px, px * pct
        klines.append(_kline(i, o, c))
        px = c
    return klines, px


def _flat(klines: list[list[Any]], start_i: int, n: int, px: float) -> None:
    """Append n doji bars pinned at one price (no wicks, no movement)."""
    for i in range(start_i, start_i + n):
        klines.append(_kline(i, px, px, hi=px, lo=px))


def _base_klines() -> list[list[Any]]:
    """A 0.3%/bar ramp where bar 38 is a wild bar whose [low, high] swallows
    the barriers of the position opened at bar 36 (the entry fires at the roll
    into bar 36; with tp=1.2% the natural take-profit would only trip at bar
    39's high tick, so at bar 38 the position is necessarily still open)."""
    klines, px = _ramp(38)          # bars 0..37
    wild = _kline(38, px, px * 1.004, hi=px * 1.05, lo=px * 0.95)
    klines.append(wild)
    px = px * 1.004
    for i in range(39, 90):
        o, c = px, px * 1.003
        klines.append(_kline(i, o, c))
        px = c
    return klines


def _cfg(fee_bps: float = 10.0, slip_bps: float = 3.0,
         sl_pct: float = 1.5, tp_pct: float = 1.2) -> BotConfig:
    # These tests exercise harness MECHANICS on synthetic klines (including the
    # short side), so they pin the legacy no-op shape explicitly — the shipped
    # BotConfig default is the verified "H" config (long_only, direction
    # filter, time stop, sigma barriers), which would change trade outcomes
    # here for reasons unrelated to what each test checks.
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
            direction_bars=0, max_hold_bars=0, long_only=False,
        ),
        risk_overrides=RiskOverrides(
            stop_loss_pct=sl_pct, take_profit_pct=tp_pct, vol_window_s=30.0,
            stop_mode="percent", stop_sigma_mult=1.5, tp_sigma_mult=1.2,
        ),
        console_log_path="/tmp/entropy_harness_test/console.log",
        trade_csv_path="/tmp/entropy_harness_test/trades.csv",
    )


def _iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def _run(klines: list[list[Any]], cfg: BotConfig, **kw: Any) -> dict[str, Any]:
    return mod.simulate(klines, cfg, run_dir="/tmp/entropy_harness_test/ledger", **kw)


# ---- the tick order itself ----------------------------------------------------


def test_build_ticks_feeds_low_before_high():
    """Pessimistic intrabar convention, at the unit level: one kline becomes
    O, L, H, C ticks (stop-tripping tick before TP-tripping tick)."""
    k = _kline(0, 100.0, 101.0, hi=101.5, lo=99.5)
    ticks = mod.build_ticks([k])
    assert [t["price"] for t in ticks] == [100.0, 99.5, 101.5, 101.0]
    # all four ticks stay inside the same 15m strategy bar
    bar = ticks[0]["ts_ns"] // int(900 * 1e9)
    assert all(t["ts_ns"] // int(900 * 1e9) == bar for t in ticks)


# ---- both-in-bar resolves stop first ------------------------------------------


def test_both_in_bar_resolves_stop_first():
    """A bar whose [low, high] contains both an open long's stop and its
    take-profit must close the position via the STOP, not the take-profit.

    Under the legacy O,H,L,C tick order the high tick hit the TP first and
    every such bar was scored a win — the inflation this convention removes."""
    klines = _base_klines()
    report = _run(klines, _cfg())
    trades = report["trades"]
    assert trades, "the ramp must produce trades"
    stops = [t for t in trades if t["exit_intent"] == "stop"]
    assert len(stops) == 1, f"exactly the wild bar may stop out: {report['exit_breakdown']}"
    stop = stops[0]
    assert stop["side"] == "LONG"
    # both barriers were inside the wild bar's range -> this is the both-in-bar case
    stop_level = stop["entry_px"] * (1 - 0.015)
    tp_level = stop["entry_px"] * (1 + 0.012)
    wild = klines[38]
    assert wild[3] <= stop_level and wild[2] >= tp_level
    # the exit fill lands on the wild bar's LOW tick (fed before the H tick)
    exit_ms = datetime.fromisoformat(stop["exit_ts"]).timestamp() * 1000.0
    assert wild[0] < exit_ms <= wild[0] + BAR_MS
    assert exit_ms - wild[0] == 1000.0
    # and the trade lost money: stop-first is the pessimistic outcome
    assert stop["pnl"] < 0


def test_stop_gap_through_keeps_the_worse_fill():
    """Worse-of convention: a stop whose bar low is deeper than the barrier
    keeps the (worse) actual fill instead of being upgraded to the level."""
    klines = _base_klines()
    report = _run(klines, _cfg())
    stop = next(t for t in report["trades"] if t["exit_intent"] == "stop")
    wild_open = klines[38][1]
    stop_level = stop["entry_px"] * (1 - 0.015)
    assert stop_level > wild_open * 0.95  # the low gapped through the barrier
    assert stop["exit_px"] == pytest.approx(wild_open * 0.95 * (1 - SLIP), rel=1e-3)
    assert stop["exit_px"] < stop_level * (1 - SLIP)


def _short_base_klines() -> list[list[Any]]:
    """Mirror of :func:`_base_klines` on a 0.3%/bar downtrend: the short entry
    fires at the roll into bar 36 and bar 38 is a wild bar whose [low, high]
    swallows the short's stop (above) AND take-profit (below)."""
    klines, px = _ramp(38, pct=0.997)   # bars 0..37
    klines.append(_kline(38, px, px * 0.996, hi=px * 1.05, lo=px * 0.95))
    px *= 0.996
    for i in range(39, 90):
        o, c = px, px * 0.997
        klines.append(_kline(i, o, c))
        px = c
    return klines


def test_both_in_bar_resolves_stop_first_for_shorts():
    """The mirror case: for a SHORT the stop sits ABOVE the entry and the
    take-profit below, so a fixed L-before-H order would resolve the TP first
    (the direction bias the round-1 review caught — it reintroduced the WR
    inflation for shorts). The wild bar must stop the position out via its
    HIGH tick, which for shorts is fed before the low tick."""
    klines = _short_base_klines()
    report = _run(klines, _cfg())
    trades = report["trades"]
    assert trades, "the downtrend must produce trades"
    shorts = [t for t in trades if t["side"] == "SHORT"]
    assert shorts, "the downtrend must produce SHORT trades"
    stops = [t for t in trades if t["exit_intent"] == "stop"]
    assert len(stops) == 1, f"exactly the wild bar may stop out: {report['exit_breakdown']}"
    stop = stops[0]
    assert stop["side"] == "SHORT"
    # both barriers were inside the wild bar's range -> the both-in-bar case
    stop_level = stop["entry_px"] * (1 + 0.015)
    tp_level = stop["entry_px"] * (1 - 0.012)
    wild = klines[38]
    assert wild[2] >= stop_level and wild[3] <= tp_level
    # the exit fill lands on the wild bar's HIGH tick — fed FIRST for shorts
    # (re-stamped to +1s so fed time stays monotonic)
    exit_ms = datetime.fromisoformat(stop["exit_ts"]).timestamp() * 1000.0
    assert wild[0] < exit_ms <= wild[0] + BAR_MS
    assert exit_ms - wild[0] == 1000.0
    # worse-of keeps the deeper BUY fill (bar high + adverse slippage)
    assert stop["exit_px"] == pytest.approx(wild[2] * (1 + SLIP), rel=1e-3)
    assert stop["exit_px"] > stop_level * (1 + SLIP)
    assert stop["pnl"] < 0


# ---- level fills, not bar extremes ---------------------------------------------


def test_take_profit_fill_clamped_to_tp_level_not_bar_high():
    """A mechanical TP fill is re-priced at the barrier level (minus the
    close-side adverse slippage), not at the tripping bar's high tick."""
    klines = _base_klines()
    report = _run(klines, _cfg())
    tps = [t for t in report["trades"] if t["exit_intent"] == "take_profit"]
    assert tps, f"expected TP exits: {report['exit_breakdown']}"
    for tp in tps:
        # vol_window_s=30 -> fewer than 5 window ticks at entry -> no volatility
        # scaling: the TP anchors exactly at entry_fill * (1 + 1.2%)
        expected = tp["entry_px"] * (1 + 0.012) * (1 - SLIP)
        assert tp["exit_px"] == pytest.approx(expected, rel=1e-3)
        # the unclamped fill would have been the tripping bar's HIGH tick minus slip
        exit_ms = datetime.fromisoformat(tp["exit_ts"]).timestamp() * 1000.0
        bar_i = round((exit_ms - T0_MS - 2000.0) / BAR_MS)  # H tick sits at +2s
        assert 0 <= bar_i < len(klines)
        bar_high = klines[bar_i][2]
        assert bar_high >= tp["entry_px"] * (1 + 0.012)  # the high reached the level
        assert tp["exit_px"] < bar_high * (1 - SLIP)


def test_strategy_and_liquidation_exits_are_never_clamped():
    """Only mechanical stop/TP intents are clamped. The end-of-test
    liquidation (intent 'close') keeps its actual mark-based fill: a position
    held into a flat tail (price pinned between stop and a far TP) is
    liquidated exactly at the mark."""
    klines, last_close = _ramp(46)
    _flat(klines, 46, 25, last_close)   # between stop (entry*0.985) and TP (entry*10%)
    report = _run(klines, _cfg(sl_pct=1.5, tp_pct=10.0))
    closes = [t for t in report["trades"] if t["exit_intent"] == "close"]
    assert closes, "the final open position must be liquidated as 'close'"
    for t in closes:
        # unclamped: liquidation fills at the mark with adverse slippage
        # (trade records round prices to 2 decimals)
        assert t["exit_px"] == pytest.approx(last_close * (1 - SLIP), rel=1e-3)
        # if the TP level (entry*1.10) had been (mis)applied, this would fail
        assert t["exit_px"] < t["entry_px"] * 1.05


# ---- zero PnL is a loss ---------------------------------------------------------


def test_zero_pnl_counts_as_a_loss():
    """With all costs zeroed, a position opened at the ramp/flat boundary and
    held into a perfectly flat market is liquidated at exactly its entry
    price: pnl == 0. Only `pnl > 0` is a win, so this trade is a loss."""
    klines, last_close = _ramp(36)      # bars 0..35
    _flat(klines, 36, 35, last_close)   # the entry fires at bar 36's open tick
    report = _run(klines, _cfg(fee_bps=0.0, slip_bps=0.0))
    assert report["metrics"]["win_definition"] == "pnl > 0"
    assert report["metrics"]["total_trades"] == 1
    trade = report["trades"][0]
    assert trade["exit_intent"] == "close"       # end-of-test liquidation
    assert trade["pnl"] == 0.0                   # entry price == liquidation mark exactly
    assert report["metrics"]["win_rate"] == 0.0  # zero PnL is a loss


# ---- long-only reporting --------------------------------------------------------


def test_long_only_metrics_match_the_long_round_trips():
    report = _run(_base_klines(), _cfg())
    trades = report["trades"]
    longs = [t for t in trades if t["side"] == "LONG"]
    long_wins = [t for t in longs if t["pnl"] > 0]
    m = report["metrics"]
    assert m["total_trades_long_only"] == len(longs)
    expected = len(long_wins) / len(longs) if longs else 0.0
    assert m["win_rate_long_only"] == pytest.approx(expected, abs=1e-4)
    # every trade on this path is long (the spot-deployable subset is everything)
    assert len(longs) == len(trades)
    assert m["win_rate_long_only"] == m["win_rate"]


# ---- warmup chaining ------------------------------------------------------------


def test_warmup_bars_seed_state_without_counting_trades():
    """--warmup-bars N: the warmup slice feeds strategy/risk state, its trades
    are not counted, and a position open at the window start is liquidated at
    the window's first tick (into the warmup ledger, not the report)."""
    klines = _base_klines()
    report = _run(klines, _cfg(sl_pct=1.5, tp_pct=10.0), warmup_bars=45)
    m = report["metrics"]
    assert report["warmup"]["bars"] == 45
    # the bar-36 entry is still open (tp 10% never hit on a 4% ramp): the
    # warmup ledger holds its open fill plus the split liquidation = 1 trade
    assert report["warmup"]["trades"] == 1
    window_start = _iso(klines[45][0])
    assert m["total_trades"] == len(report["trades"])
    assert report["trades"], "the evaluated window must still trade"
    for t in report["trades"]:
        assert t["entry_ts"] >= window_start
    # the warmup entry must not leak into the evaluated trade list
    warmup_entry_ts = _iso(klines[36][0])
    assert all(t["entry_ts"] != warmup_entry_ts for t in report["trades"])
    assert m["halted"] is False


def test_warmup_zero_keeps_legacy_behavior():
    """warmup_bars=0 must be byte-identical to the legacy cold start."""
    klines = _base_klines()
    r_default = _run(klines, _cfg())
    r_zero = _run(klines, _cfg(), warmup_bars=0)
    assert r_zero == r_default
