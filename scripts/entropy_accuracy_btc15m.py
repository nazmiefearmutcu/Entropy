#!/usr/bin/env python3
"""Entropy accuracy test on real Binance BTCUSDT 15m bars.

Drives the FINAL-STATE bot engine (BotRunner + consensus/ema_cross strategies +
cost-aware risk layer) with real Binance 15m klines:

  * paper account, --cash USD (default 100)
  * symbol binance-spot:BTCUSDT -> Binance spot taker schedule
    (10 bps fee + 3 bps slippage per side, 26 bps round trip)
  * reference bars = 15m (timeframe="15m", bar_s=900.0)
  * risk profile: per-trade % of equity (never all-in), exposure cap,
    daily-loss kill switch

Usage:
  .venv/bin/python scripts/entropy_accuracy_btc15m.py --bars 2880 --out /tmp/entropy_accuracy/30d
  .venv/bin/python scripts/entropy_accuracy_btc15m.py --bars 96 --end-date 2026-08-02T00:00:00Z --out /tmp/entropy_accuracy/1d

Honest-measurement conventions (these intentionally lower the measured win
rate versus the original harness — that is the point):

  * Pessimistic intrabar barrier resolution. Each 15m bar is fed as 4 ticks:
    the open, then the low/high pair in the DIRECTION-AWARE pessimistic order
    (an open long feeds L before H, an open short feeds H before L; the order
    is chosen after the open tick so just-opened positions are covered), then
    the close. Whenever a bar's range contains both an open position's stop
    and its take-profit, the STOP resolves first — for either direction (a
    fixed order is only pessimistic for longs; L-before-H would resolve the
    TP first for shorts).
  * Level fills, not bar extremes. Exit fills from mechanical stop/TP intents
    are clamped, when pairing, to the barrier level the risk layer anchored at
    entry (worse of level and actual fill, both with the close-side adverse
    slippage the executor charges). Strategy exits (intent "close") and the
    end-of-test liquidation are untouched.
  * Zero PnL is a loss: only ``pnl > 0`` counts as a win (reported as
    ``win_definition``).
  * ``--warmup-bars N`` (default 100) seeds strategy/risk state with N extra
    bars before the evaluated window; warmup trades are not counted and a
    position still open at the window start is liquidated at the first
    evaluated tick. ``--warmup-bars 0`` restores the old cold-start behavior.
  * Long-only metrics (``win_rate_long_only``) report the spot-deployable
    subset alongside the total.
  * Accuracy levers; the defaults ARE the shipped configuration (verified "H",
    see PROJECT.md "Win rate > 60% OOS"): ``--long-only`` makes the strategy
    never emit ENTER_SHORT (spot shorts are not executable live),
    ``--max-hold-bars N`` adds a time stop, and ``--stop-mode sigma`` anchors
    stop/TP at ``--stop-sigma-mult``/``--tp-sigma-mult`` times the entry bar's
    per-bar return RMS instead of the fixed percents. Pass
    ``--stop-mode percent --max-hold-bars 0 --direction-bars 0`` (and no
    ``--long-only``) to reproduce the pre-H legacy behavior.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running straight from a checkout without an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from entropy.bot.calibration import DummyLedger
from entropy.bot.config import BotConfig, ConsensusConfig, MarketCostConfig, RiskOverrides
from entropy.bot.orders import Fill, OrderIntent, OrderSide
from entropy.bot.portfolio import PositionSide
from entropy.bot.runner import BotRunner

SYMBOL = "binance-spot:BTCUSDT"
RAW = "BTCUSDT"
INTERVAL = "15m"
BAR_MS = 15 * 60 * 1000
_NS = 1_000_000_000

KLINES_URL = "https://api.binance.com/api/v3/klines"


def resolve_symbol(raw: str) -> str:
    return f"binance-spot:{raw.upper()}"


def parse_end(s: str) -> int:
    if s in ("", "now"):
        return int(time.time() * 1000)
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(bars: int, end_ms: int, cache: Path | None = None,
                 raw: str = RAW) -> list[list[Any]]:
    if cache is not None and cache.exists():
        data = json.loads(cache.read_text())
        if data.get("meta", {}).get("bars") == bars:
            return data["klines"]
    start_ms = end_ms - bars * BAR_MS
    got: list[list[Any]] = []
    cur = start_ms
    while len(got) < bars:
        url = (
            f"{KLINES_URL}?symbol={raw}&interval={INTERVAL}"
            f"&startTime={cur}&limit=1000"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "entropy-accuracy/1.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    chunk = json.loads(resp.read().decode())
                break
            except Exception as exc:
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        if not chunk:
            break
        got.extend(chunk)
        nxt = chunk[-1][6] + 1
        if nxt <= cur:
            break
        cur = nxt
    # Keep only fully closed bars inside [start, end].
    closed = [k for k in got if k[0] + BAR_MS <= end_ms]
    keep = closed[-bars:]
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"meta": {"bars": bars, "end_ms": end_ms},
                                     "klines": keep}))
    return keep


def build_ticks(klines: list[list[Any]], symbol: str = SYMBOL) -> list[dict[str, Any]]:
    """One 15m kline -> 4 ticks in canonical order O, L, H, C, grouped 4 per
    bar (simulate() re-stamps and swaps the L/H pair per bar by open-position
    DIRECTION so the STOP always resolves before the TP inside a bar: L first
    for longs, H first for shorts — see BotRunner feed in simulate).

    With the historical O,H,L,C order a take-profit inside the bar always
    resolved BEFORE a stop also inside the bar, at the bar's extreme price.
    The remaining level-fill clamp in simulate() keeps mechanical fills
    honest. Strategy bars bucket on ts//900s so all four ticks land in the
    same 15m bar regardless of order.
    """
    ticks: list[dict[str, Any]] = []
    for k in klines:
        open_ms = int(k[0])
        open_px, high, low, close = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        # open, low, high, close (direction-aware stop-first ordering in feed)
        for off_s, px in ((0, open_px), (1, low), (2, high), (3, close)):
            ticks.append({
                "symbol": symbol, "price": px, "amount": 1.0, "side": "buy",
                "ts_ns": (open_ms + off_s * 1000) * 1_000_000,
            })
    return ticks


def utc_day(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / _NS, tz=timezone.utc).strftime("%Y-%m-%d")


class _BarrierLedger(DummyLedger):
    """DummyLedger that remembers the stop/take-profit levels the risk layer
    anchored at each open fill.

    The runner prices mechanical exits at the mark that tripped them, so a
    stop/TP fill recorded inside a wide bar carries the bar's extreme, not the
    barrier. The levels (``RiskManager.stop_tp_prices`` on the
    slippage-adjusted entry fill, volatility scaling included) are captured
    from the live position at open-fill time — after ``portfolio.open`` — so
    the anchoring math is mirrored exactly, and the pairing step in
    :func:`simulate` can clamp mechanical exit fills back to the barrier.

    INVARIANT: barrier levels are immutable after open. The risk layer has no
    trailing stop and never re-anchors stop/TP while a position is open, which
    is the only reason capturing once at open is valid — if re-anchoring or a
    risk-layer trail is ever added, this capture must move to per-exit time.
    """

    def __init__(self, runner: BotRunner) -> None:
        super().__init__()
        self._runner = runner
        # barrier levels captured at each OPEN fill, aligned 1:1 with
        # self.fills (a symbol-keyed dict would be overwritten by later
        # trades before the pairing walk ever reads it)
        self.open_levels: list[tuple[float, float] | None] = []

    def record_fill(self, fill: Any, intent: Any) -> None:
        levels: tuple[float, float] | None = None
        if getattr(intent, "value", intent) == "open":
            pos = self._runner.portfolio.positions.get(fill.symbol)
            if pos is not None:
                levels = (pos.stop_px, pos.tp_px)
        self.open_levels.append(levels)
        super().record_fill(fill, intent)


def _liquidate_open_positions(runner: BotRunner, ledger: DummyLedger, ts_ns: int,
                              *, notify_reason: str | None = None) -> None:
    """Flatten every open position at the mark, with close-side adverse
    slippage and fees — the end-of-test liquidation math. Also used at the
    warmup/window boundary so a position still open when the evaluated window
    begins is liquidated at the window's first tick (its fill lands in the
    warmup ledger and is not counted)."""
    executor = runner.executor
    for symbol in list(runner.portfolio.positions):
        pos = runner.portfolio.positions[symbol]
        mark_px = runner.portfolio.mark_of(symbol)
        resolved = executor.cost_model.for_symbol(symbol)
        fee_bps = resolved.fee_bps
        slip_bps = resolved.slippage_bps
        assert fee_bps is not None and slip_bps is not None
        close_side = OrderSide.SELL if pos.side is PositionSide.LONG else OrderSide.BUY
        slip = mark_px * (slip_bps / 10_000.0)
        fill_px = mark_px - slip if close_side is OrderSide.SELL else mark_px + slip
        fee = abs(fill_px * pos.qty) * (fee_bps / 10_000.0)
        ledger.record_fill(
            Fill(order_id=f"liq-{symbol}", symbol=symbol, side=close_side, qty=pos.qty,
                 price=fill_px, fee=fee, slippage=slip, ts_ns=ts_ns),
            OrderIntent.CLOSE,
        )
        runner.portfolio.close(symbol, fill_px, ts_ns, fee=fee)
        if notify_reason is not None:
            # the run continues after a warmup-boundary liquidation: re-arm the
            # strategies exactly as the runner does for risk-originated closes
            runner._notify_closed(symbol, notify_reason)


def simulate(klines: list[list[Any]], cfg: BotConfig,
             run_dir: str = "/tmp/entropy_accuracy/_sim/ledger",
             trade_csv: str = "/tmp/entropy_accuracy/_sim/trades.csv",
             symbol: str = SYMBOL, warmup_bars: int = 0) -> dict[str, Any]:
    """Feed real 15m klines through the final-state BotRunner and compute the
    full accuracy report. Mirrors the metric math of calibration.run_backtest
    (end-of-test liquidation, paired fills, costs).

    ``warmup_bars`` feeds the FIRST ``warmup_bars`` klines through the runner
    without counting their trades (strategy/risk state carries into the
    evaluated window; positions open at the window start are liquidated at the
    window's first tick). ``warmup_bars=0`` is the legacy cold start.
    """
    out_dir = Path(run_dir).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = BotRunner(cfg, run_dir=str(out_dir / "ledger"))
    warm_ledger = _BarrierLedger(runner)
    dummy = _BarrierLedger(runner)
    runner.ledger = warm_ledger  # type: ignore[assignment]

    # Split into warmup and evaluated slices (keep at least one evaluated bar).
    warmup_bars = max(0, min(warmup_bars, len(klines) - 1)) if klines else 0
    warm_ticks = build_ticks(klines[:warmup_bars], symbol=symbol)
    eval_ticks = build_ticks(klines[warmup_bars:], symbol=symbol)

    def _feed(ticks: list[dict[str, Any]]):
        """Feed ticks bar by bar (build_ticks yields exactly 4 per bar: O,
        L, H, C). The L/H order is decided PER BAR, after the open tick, from
        the direction of the position open at that moment — a bar-roll entry
        can only fire on the O tick (strategy evaluation runs on bucket rolls
        only), so checking after the O tick covers just-opened positions too:

          long  -> L before H: the stop (below) tick trips before the TP tick
          short -> H before L: the stop (above) tick trips before the TP tick
          flat  -> L, H, C    (no barriers; the order is neutral)

        A fixed order cannot be pessimistic for both directions: L-before-H
        resolves the take-profit FIRST for shorts whose stop and TP both sit
        inside one bar — the mirror image of the long-side O,H,L,C bias this
        harness exists to remove. Tick timestamps are re-stamped on swap so
        the fed time stays monotonic."""
        curve: list[tuple[int, float, float]] = []
        max_open = 0
        max_exp = 0.0
        prev_day = None

        def _on(t: dict[str, Any]) -> None:
            nonlocal max_open, max_exp, prev_day
            day = utc_day(t["ts_ns"])
            if day != prev_day:
                # Backtest is fast-forwarded, so the runner's wall-clock day
                # rollover never fires; reset per tick-day to keep the daily-loss
                # kill-switch genuinely daily (same semantics as live).
                if prev_day is not None:
                    runner.portfolio.reset_day()
                    runner.risk.reset_day()
                runner._utc_day = day
                prev_day = day
            runner.on_trade(t["symbol"], t["price"], t["amount"], t["side"], t["ts_ns"])
            snap = runner.portfolio.snapshot(t["ts_ns"])
            curve.append((t["ts_ns"], snap.equity, snap.open_count))
            if snap.open_count > max_open:
                max_open = snap.open_count
            exp = runner.portfolio.exposure()
            if snap.equity > 0 and exp / snap.equity > max_exp:
                max_exp = exp / snap.equity

        for i in range(0, len(ticks), 4):
            bar = ticks[i:i + 4]
            _on(bar[0])
            pos = runner.portfolio.positions.get(bar[0]["symbol"])
            rest = bar[1:]
            if pos is not None and pos.side is PositionSide.SHORT and len(rest) == 3:
                # short: feed the HIGH tick (stop side) before the LOW tick
                # (TP side); re-stamp the swapped ticks' timestamps so time
                # stays monotonic within the bar (ticks are spaced 1s apart)
                h, low = dict(bar[2]), dict(bar[1])
                h["ts_ns"] = bar[0]["ts_ns"] + _NS
                low["ts_ns"] = bar[0]["ts_ns"] + 2 * _NS
                rest = [h, low, bar[3]]
            for t in rest:
                _on(t)
        return curve, max_open, max_exp

    warmup_trades = 0
    if warmup_bars:
        print(f"[entropy-accuracy] warmup: feeding {len(warm_ticks)} ticks "
              f"({warmup_bars} bars) ...")
        _feed(warm_ticks)
        warmup_trades = len(warm_ledger.fills) // 2
        if runner.portfolio.positions:
            # flatten at the window's first tick, before the evaluated feed
            _liquidate_open_positions(runner, warm_ledger, eval_ticks[0]["ts_ns"],
                                      notify_reason="warmup_liquidation")
    runner.ledger = dummy  # type: ignore[assignment]  # evaluated trades only

    print(f"[entropy-accuracy] feeding {len(eval_ticks)} evaluated ticks ...")
    equity_curve, max_open, max_exposure_pct = _feed(eval_ticks)

    # ---- end-of-test liquidation (mirrors calibration.run_backtest) ----
    final_ts = eval_ticks[-1]["ts_ns"]
    _liquidate_open_positions(runner, dummy, final_ts)

    snap = runner.portfolio.snapshot(final_ts)
    total_trades = len(dummy.fills) // 2
    costs_paid = sum(f.fee + f.slippage * f.qty for f, _ in dummy.fills)
    # Close-side adverse slippage the executor charges (PaperExecutor: slip =
    # order price * slippage_bps/1e4 against the trade direction) — used to
    # re-price mechanical stop/TP fills at their barrier level below.
    resolved_costs = runner.executor.cost_model.for_symbol(symbol)
    slip_bps = (
        resolved_costs.slippage_bps if resolved_costs.slippage_bps is not None
        else cfg.slippage_bps
    )
    fee_bps_close = (
        resolved_costs.fee_bps if resolved_costs.fee_bps is not None else cfg.fee_bps
    )
    wins = losses = 0
    long_trades = long_wins = 0
    total_profit = total_loss = 0.0
    closed_pnls: list[float] = []
    trades: list[dict[str, Any]] = []
    positions_history: dict[str, Any] = {}
    # symbol -> levels captured (at record time) at that symbol's open fill
    trade_levels: dict[str, tuple[float, float] | None] = {}
    exit_breakdown: Counter = Counter()
    hold_bars: list[float] = []
    for i, (fill, intent) in enumerate(dummy.fills):
        if intent.value == "open":
            positions_history[fill.symbol] = fill
            trade_levels[fill.symbol] = dummy.open_levels[i]
        else:
            entry = positions_history.pop(fill.symbol)
            exit_px = fill.price
            close_fee = fill.fee
            levels = trade_levels.pop(fill.symbol, None)
            if intent.value in ("stop", "take_profit") and levels is not None:
                # Level fills, not bar extremes: clamp mechanical stop/TP exit
                # fills to the barrier level the risk layer anchored at entry,
                # taking the WORSE of the level fill and the actual fill (both
                # carry the adverse close-side slippage). Strategy exits and
                # the end-of-test liquidation (intent "close") are untouched.
                stop_px, tp_px = levels
                level = stop_px if intent.value == "stop" else tp_px
                if fill.side.value == "sell":  # closing a long: worse = lower
                    exit_px = min(level * (1.0 - slip_bps / 10_000.0), fill.price)
                else:                          # closing a short: worse = higher
                    exit_px = max(level * (1.0 + slip_bps / 10_000.0), fill.price)
                # the recorded fee was charged on the actual (extreme) fill
                # notional; re-price it at the clamped exit price
                close_fee = abs(exit_px * fill.qty) * (fee_bps_close / 10_000.0)
            qty = fill.qty
            side_str = "LONG" if entry.side.value == "buy" else "SHORT"
            if side_str == "LONG":
                pnl = (exit_px - entry.price) * qty - entry.fee - close_fee
            else:
                pnl = (entry.price - exit_px) * qty - entry.fee - close_fee
            closed_pnls.append(pnl)
            trades.append({
                "side": side_str,
                "entry_px": round(entry.price, 2), "exit_px": round(exit_px, 2),
                "qty": round(qty, 6),
                "entry_ts": datetime.fromtimestamp(entry.ts_ns / _NS, tz=timezone.utc).isoformat(),
                "exit_ts": datetime.fromtimestamp(fill.ts_ns / _NS, tz=timezone.utc).isoformat(),
                "exit_intent": intent.value,
                "pnl": round(pnl, 4), "pnl_pct": round(pnl / (qty * entry.price) * 100.0, 3),
            })
            exit_breakdown[intent.value] += 1
            hold_bars.append((fill.ts_ns - entry.ts_ns) / (900 * _NS))
            if side_str == "LONG":
                long_trades += 1
                if pnl > 0:
                    long_wins += 1
            # Zero PnL is a loss: only strictly positive pnl counts as a win
            # (conservative — costs make exact-zero rare but possible).
            if pnl > 0:
                wins += 1
                total_profit += pnl
            else:
                losses += 1
                total_loss += abs(pnl)

    win_rate = wins / total_trades if total_trades else 0.0
    win_rate_long_only = long_wins / long_trades if long_trades else 0.0
    profit_factor = total_profit / total_loss if total_loss > 0 else (total_profit if total_profit > 0 else 1.0)
    if len(closed_pnls) > 1:
        mean_pnl = sum(closed_pnls) / len(closed_pnls)
        var = sum((x - mean_pnl) ** 2 for x in closed_pnls) / (len(closed_pnls) - 1)
        sharpe = (mean_pnl / math.sqrt(var)) * math.sqrt(252) if var > 0 else 0.0
    else:
        sharpe = 0.0

    # ---- drawdown + daily returns ----
    peak = -math.inf
    max_dd = 0.0
    for _, eq, _ in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0.0)

    by_day: dict[str, list[float]] = {}
    for ts_ns, eq, _ in equity_curve:
        by_day.setdefault(utc_day(ts_ns), []).append(eq)
    daily = {
        day: {"start": eqs[0], "end": eqs[-1],
              "return_pct": (eqs[-1] / eqs[0] - 1.0) * 100.0 if eqs[0] else 0.0}
        for day, eqs in sorted(by_day.items())
    }
    day_returns = [d["return_pct"] for d in daily.values()]
    total_notional = sum(
        abs(f.price * f.qty) for f, _ in dummy.fills
    )
    final_equity = snap.equity
    total_return = (final_equity / cfg.starting_cash - 1.0) * 100.0

    report = {
        "metrics": {
            "final_equity": round(final_equity, 4),
            "total_return_pct": round(total_return, 4),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "win_definition": "pnl > 0",
            "win_rate_long_only": round(win_rate_long_only, 4),
            "total_trades_long_only": long_trades,
            "profit_factor": round(profit_factor, 4),
            "sharpe": round(sharpe, 4),
            "costs_paid": round(costs_paid, 4),
            "max_drawdown_pct": round(max_dd * 100.0, 4),
            "max_exposure_pct_observed": round(max_exposure_pct * 100.0, 2),
            "max_concurrent_open": max_open,
            "total_notional_traded": round(total_notional, 4),
            "notional_turnover_x": round(total_notional / cfg.starting_cash, 2),
            "halted": runner.risk.halted,
            "best_day_pct": round(max(day_returns), 4) if day_returns else 0.0,
            "worst_day_pct": round(min(day_returns), 4) if day_returns else 0.0,
        },
        "daily": daily,
        "trades": trades,
        "exit_breakdown": dict(exit_breakdown),
        "avg_hold_bars": round(sum(hold_bars) / len(hold_bars), 2) if hold_bars else 0.0,
        "rejects": dict(Counter(r for _, r in dummy.rejects)),
        "warmup": {"bars": warmup_bars, "trades": warmup_trades},
    }
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=2880,
                    help="number of EVALUATED 15m bars (warmup bars are extra)")
    ap.add_argument("--warmup-bars", type=int, default=100,
                    help="extra bars fed before the evaluated window to seed "
                         "strategy/risk state; their trades are NOT counted and "
                         "a position open at the window start is liquidated at "
                         "the first evaluated tick (0 = legacy cold start)")
    ap.add_argument("--symbol", default="BTCUSDT",
                    help="Binance spot symbol, e.g. BTCUSDT, ETHUSDT")
    ap.add_argument("--end-date", default="now")
    ap.add_argument("--out", default="/tmp/entropy_accuracy/run")
    ap.add_argument("--cash", type=float, default=100.0)
    ap.add_argument("--per-trade-pct", type=float, default=10.0)
    ap.add_argument("--max-concurrent", type=int, default=4)
    ap.add_argument("--stop-loss-pct", type=float, default=1.5)
    ap.add_argument("--take-profit-pct", type=float, default=1.2)
    ap.add_argument("--max-total-exposure-pct", type=float, default=40.0)
    ap.add_argument("--max-daily-loss-pct", type=float, default=40.0)
    ap.add_argument("--cooldown-s", type=float, default=180.0)
    ap.add_argument("--min-volatility-pct", type=float, default=0.05)
    ap.add_argument("--vol-window-s", type=float, default=900.0)
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--exit-mode", default="trail")
    ap.add_argument("--min-hold-bars", type=int, default=5)
    ap.add_argument("--cooldown-bars", type=int, default=4)
    ap.add_argument("--cost-edge-mult", type=float, default=1.0)
    ap.add_argument("--move-floor", type=float, default=0.0003)
    ap.add_argument("--vote-mode", default="adaptive")
    ap.add_argument("--normalize", default="total")
    ap.add_argument("--min-participation", type=float, default=0.5)
    ap.add_argument("--direction-bars", type=int, default=20,
                    help="trend filter: enter only in the slow-EMA slope "
                         "direction over this many bars (0 = off)")
    ap.add_argument("--confirm-bars", type=int, default=2)
    ap.add_argument("--trail-pct", type=float, default=0.3)
    ap.add_argument("--long-only", action="store_true", default=True,
                    help="never emit ENTER_SHORT (default on: spot-deployable "
                         "subset only; pass --allow-short to disable)")
    ap.add_argument("--allow-short", dest="long_only", action="store_false",
                    help="re-enable the short leg (legacy behavior)")
    ap.add_argument("--max-hold-bars", type=int, default=96,
                    help="time stop: exit after N completed bars in a trade "
                         "(0 = off; must be 0 or >= --min-hold-bars)")
    ap.add_argument("--stop-mode", choices=("percent", "sigma"), default="sigma",
                    help="barrier anchoring: sigma-scaled from the entry bar's "
                         "return RMS (default) or fixed percents (legacy)")
    ap.add_argument("--stop-sigma-mult", type=float, default=5.0,
                    help="stop distance = mult * sigma (sigma mode only)")
    ap.add_argument("--tp-sigma-mult", type=float, default=4.0,
                    help="take-profit distance = mult * sigma (sigma mode only)")
    ap.add_argument("--strategy", action="append", default=None,
                    help="strategy name (repeatable); default consensus")
    args = ap.parse_args()

    symbol = resolve_symbol(args.symbol)
    raw = args.symbol.upper()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    end_ms = parse_end(args.end_date)
    cache = None if args.skip_fetch else out_dir / "klines.json"
    total_bars = args.bars + max(0, args.warmup_bars)
    print(f"[entropy-accuracy] fetching {total_bars} x 15m {raw} bars ending {end_ms} ...")
    klines = fetch_klines(total_bars, end_ms, cache, raw=raw)
    warmup_bars = max(0, min(args.warmup_bars, len(klines) - 1)) if klines else 0
    eval_klines = klines[warmup_bars:]
    print(f"[entropy-accuracy] got {len(eval_klines)} evaluated closed bars "
          f"(+{warmup_bars} warmup) "
          f"({datetime.fromtimestamp(eval_klines[0][0]/1000, tz=timezone.utc):%Y-%m-%d %H:%M} -> "
          f"{datetime.fromtimestamp(klines[-1][6]/1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC)")

    strategies = tuple(args.strategy) if args.strategy else ("consensus",)
    cfg = BotConfig(
        mode="paper",
        starting_cash=args.cash,
        strategies=strategies,
        symbols=(symbol,),
        ema_symbol=symbol,
        ema_fast=9,
        ema_slow=21,
        momentum_min_pct=0.15,
        timeframe="15m",
        bar_s=900.0,
        warmup=False,
        market_costs=MarketCostConfig(),
        cost_aware=True,
        cost_edge_mult=args.cost_edge_mult,
        consensus=ConsensusConfig(
            threshold=args.threshold,
            exit_mode=args.exit_mode,
            min_hold_bars=args.min_hold_bars,
            cooldown_bars=args.cooldown_bars,
            move_floor=args.move_floor,
            vote_mode=args.vote_mode,
            normalize=args.normalize,
            min_participation=args.min_participation,
            direction_bars=args.direction_bars,
            confirm_bars=args.confirm_bars,
            trail_pct=args.trail_pct,
            max_hold_bars=args.max_hold_bars,
            long_only=args.long_only,
        ),
        risk_overrides=RiskOverrides(
            per_trade_pct=args.per_trade_pct,
            max_concurrent=args.max_concurrent,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            max_total_exposure_pct=args.max_total_exposure_pct,
            max_daily_loss_pct=args.max_daily_loss_pct,
            cooldown_s=args.cooldown_s,
            min_volatility_pct=args.min_volatility_pct,
            vol_window_s=args.vol_window_s,
            stop_mode=args.stop_mode,
            stop_sigma_mult=args.stop_sigma_mult,
            tp_sigma_mult=args.tp_sigma_mult,
        ),
        console_log_path=str(out_dir / "console.log"),
        trade_csv_path=str(out_dir / "trades.csv"),
    )

    report = simulate(klines, cfg, run_dir=str(out_dir / "ledger"),
                      trade_csv=str(out_dir / "trades.csv"), symbol=symbol,
                      warmup_bars=args.warmup_bars)
    report["config"] = {
        "symbol": symbol, "interval": INTERVAL, "bars": len(eval_klines),
        "warmup_bars": warmup_bars,
        "start_utc": datetime.fromtimestamp(eval_klines[0][0] / 1000, tz=timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(klines[-1][6] / 1000, tz=timezone.utc).isoformat(),
        "starting_cash": args.cash,
        "timeframe": "15m", "bar_s": 900.0,
        "strategies": list(strategies),
        "market_costs": {"fee_bps": 10.0, "slippage_bps": 3.0, "round_trip_bps": 26.0},
        "risk": {
            "per_trade_pct": args.per_trade_pct, "max_concurrent": args.max_concurrent,
            "stop_loss_pct": args.stop_loss_pct, "take_profit_pct": args.take_profit_pct,
            "max_total_exposure_pct": args.max_total_exposure_pct,
            "max_daily_loss_pct": args.max_daily_loss_pct,
            "cooldown_s": args.cooldown_s,
            "stop_mode": args.stop_mode,
            "stop_sigma_mult": args.stop_sigma_mult,
            "tp_sigma_mult": args.tp_sigma_mult,
        },
        "long_only": args.long_only,
        "max_hold_bars": args.max_hold_bars,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))

    # ---- human-readable print ----
    m = report["metrics"]
    print(f"\n=== ENTROPY ACCURACY TEST — {raw} 15m (real Binance data) ===")
    print(f"window       : {report['config']['start_utc']} -> {report['config']['end_utc']} UTC "
          f"({report['config']['bars']} bars)")
    print(f"account      : ${args.cash:.2f} paper, {symbol}")
    print(f"costs        : Binance spot {report['config']['market_costs']}")
    print(f"risk         : {args.per_trade_pct}%/trade, max {args.max_concurrent} open, "
          f"exposure <= {args.max_total_exposure_pct}%, daily loss <= {args.max_daily_loss_pct}%")
    print(f"levers       : long_only={args.long_only}, max_hold_bars={args.max_hold_bars}, "
          f"stop_mode={args.stop_mode}"
          + (f" (stop {args.stop_sigma_mult}x / tp {args.tp_sigma_mult}x sigma)"
             if args.stop_mode == "sigma" else ""))
    print(f"final equity : ${m['final_equity']:.2f}  (return {m['total_return_pct']:+.2f}%)")
    print(f"accuracy     : win rate {m['win_rate']*100:.1f}%  ({m['total_trades']} closed trades, "
          f"win = pnl > 0)")
    print(f"long-only    : win rate {m['win_rate_long_only']*100:.1f}%  "
          f"({m['total_trades_long_only']} closed long trades, spot-deployable)")
    print(f"quality      : profit factor {m['profit_factor']:.2f}, sharpe {m['sharpe']:.2f}")
    print(f"costs paid   : ${m['costs_paid']:.2f}  (turnover {m['notional_turnover_x']}x cash)")
    print(f"risk         : max DD {m['max_drawdown_pct']:.2f}%, max exposure {m['max_exposure_pct_observed']:.1f}%, "
          f"max open {m['max_concurrent_open']}, halted={m['halted']}")
    print(f"day range    : best {m['best_day_pct']:+.2f}% / worst {m['worst_day_pct']:+.2f}%  "
          f"(target: +10% or wipeout)")
    if report["rejects"]:
        top = sorted(report["rejects"].items(), key=lambda kv: -kv[1])[:5]
        print("top rejects  : " + "; ".join(f"{r} x{c}" for r, c in top))
    print(f"exits        : {report['exit_breakdown']}  (avg hold {report['avg_hold_bars']} bars)")
    print(f"report       : {out_dir}/report.json")


if __name__ == "__main__":
    main()
