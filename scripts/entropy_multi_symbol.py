#!/usr/bin/env python3
"""Multi-symbol shared-equity Entropy accuracy run (real Binance 15m bars).

Drives ONE BotRunner with N symbols so concurrency caps, the exposure cap and
the daily-loss kill switch bind on SHARED equity — the live-honest simulation
the single-symbol harness cannot measure. Tick ordering, direction-aware
stop-first resolution, level-fill clamping, warmup chaining and the end-of-test
liquidation mirror scripts/entropy_accuracy_btc15m.py exactly (PARITY: a
single-symbol run through this script must reproduce that harness bit-for-bit).

Usage:
  .venv/Scripts/python.exe scripts/entropy_multi_symbol.py \
      --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT \
      --bars 2880 --per-trade-pct 15 --max-total-exposure-pct 60 \
      --out /tmp/entropy_multi/pool30d_k1.5
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

_HARNESS = REPO / "scripts" / "entropy_accuracy_btc15m.py"
_spec = importlib.util.spec_from_file_location("entropy_accuracy_harness", _HARNESS)
ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ha)

from entropy.bot.config import BotConfig, ConsensusConfig, MarketCostConfig, RiskOverrides
from entropy.bot.orders import OrderIntent
from entropy.bot.portfolio import PositionSide
from entropy.bot.risk.manager import RiskDecision
from entropy.bot.signals import SignalAction

_NS = 1_000_000_000


def _ema_prefix(closes: list[float], n: int) -> list[float | None]:
    """EMA(n) prefix array: out[i] = EMA over closes[0..i-1] (None until n
    values consumed). Seed = SMA of the first n closes (repo indicator
    convention), then standard EMA with alpha=2/(n+1). No lookahead: out[i]
    never sees closes[i]."""
    out: list[float | None] = [None] * (len(closes) + 1)
    if n <= 0 or len(closes) < n:
        return out
    sma = sum(closes[:n]) / n
    out[n] = sma
    a = 2.0 / (n + 1)
    e = sma
    for i in range(n, len(closes)):
        e = e * (1.0 - a) + closes[i] * a
        out[i + 1] = e
    return out


def build_cfg(raws: list[str], args, out_dir: Path) -> BotConfig:
    symbols = tuple(ha.resolve_symbol(r) for r in raws)
    return BotConfig(
        mode="paper",
        starting_cash=args.cash,
        strategies=("consensus",),
        symbols=symbols,
        ema_symbol=symbols[0],
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
            risk_trail_pct=args.risk_trail_pct,
        ),
        console_log_path=str(out_dir / "console.log"),
        trade_csv_path=str(out_dir / "trades.csv"),
    )


def simulate_multi(per_symbol_klines: dict[str, list[list[Any]]], cfg: BotConfig,
                   run_dir: str, warmup_bars: int, entry_grace_bars: int,
                   raws: list[str], btc_gate_bars: int = 0,
                   gate_slope_bars: int = 0, stop_fuse: tuple[int, int] | None = None) -> dict[str, Any]:
    out_dir = Path(run_dir).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = ha.BotRunner(cfg, run_dir=str(out_dir / "ledger"))
    warm_ledger = ha._BarrierLedger(runner)
    dummy = ha._BarrierLedger(runner)
    runner.ledger = warm_ledger  # type: ignore[assignment]

    bar_ns = int(cfg.bar_seconds() * _NS)
    _orig_check_exits = runner.risk.check_exits

    # ---- Pool-level entry gates (pre-registered v0/v1/v2). Exits always pass.
    # v0: BTC last-completed close < BTC EMA(n)  [btc_gate_bars]
    # v1: v0 AND EMA(n) fell over the last `gate_slope_bars` bars
    # v2: >= stop_fuse[0] STOP exits within the last stop_fuse[1] bars
    # All conditions use only completed history at decision time (no lookahead).
    gate_closed_bars = 0
    gate_eval_bars = 0
    fuse_active_bars = 0
    stop_events: list[int] = []
    state = {"g": -1}
    need_wrapper = btc_gate_bars > 0 or stop_fuse is not None
    if need_wrapper:
        if btc_gate_bars > 0:
            btc_closes = [float(k[4]) for k in per_symbol_klines["BTCUSDT"]]
            ema_pref = _ema_prefix(btc_closes, btc_gate_bars)

        def _gate_note_bar(g: int) -> None:
            state["g"] = g

        def _gate_is_closed(ts_ns: int) -> str | None:
            if btc_gate_bars > 0:
                g = state["g"]
                if g < 1:
                    return None
                e = ema_pref[g] if g < len(ema_pref) else None
                if e is None or btc_closes[g - 1] >= e:
                    return None
                if gate_slope_bars > 0:
                    j = g - gate_slope_bars
                    past = ema_pref[j] if 0 <= j < len(ema_pref) else None
                    if past is not None and e >= past:
                        return None  # EMA rising: bounce phase, gate open
                return "btc-regime gate closed"
            return None

        def _fuse_is_closed(ts_ns: int) -> str | None:
            if stop_fuse is None:
                return None
            count, window_bars = stop_fuse
            lo = ts_ns - window_bars * bar_ns
            recent = sum(1 for e in stop_events if e > lo)
            return "stop-fuse active" if recent >= count else None

        _orig_evaluate = runner.risk.evaluate

        def _evaluate(signal, portfolio, mark_px, ts_ns):
            nonlocal gate_closed_bars, gate_eval_bars, fuse_active_bars
            if signal.action is not SignalAction.EXIT:
                gate_eval_bars += 1
                reason = _gate_is_closed(ts_ns) or _fuse_is_closed(ts_ns)
                if reason is not None:
                    if reason == "stop-fuse active":
                        fuse_active_bars += 1
                    else:
                        gate_closed_bars += 1
                    return RiskDecision(False, None, reason)
            return _orig_evaluate(signal, portfolio, mark_px, ts_ns)

        runner.risk.evaluate = _evaluate  # type: ignore[method-assign]

    def _check_exits(portfolio, ts_ns):
        orders = _orig_check_exits(portfolio, ts_ns)
        if not orders:
            return orders
        out: list[Any] = []
        for o in orders:
            pos = portfolio.positions.get(o.symbol)
            if pos is None:
                continue
            if o.intent is OrderIntent.STOP and ha._grace_exempt(
                pos.entry_ts_ns, ts_ns, bar_ns, entry_grace_bars
            ):
                continue  # mechanical stop suppressed during entry grace
            if stop_fuse is not None and o.intent is OrderIntent.STOP:
                stop_events.append(ts_ns)  # actual emitted stop exit
            runner.ledger.exit_levels.append((pos.stop_px, pos.tp_px))  # type: ignore[attr-defined]
            out.append(o)
        return out

    runner.risk.check_exits = _check_exits  # type: ignore[method-assign]

    # Per-symbol ticks: warmup slice + evaluated slice (same bars across symbols
    # — Binance majors have identical 15m history lengths; verified by caller).
    warm_ticks_by: dict[str, list[dict[str, Any]]] = {}
    eval_ticks_by: dict[str, list[dict[str, Any]]] = {}
    for raw, klines in per_symbol_klines.items():
        sym = ha.resolve_symbol(raw)
        wb = max(0, min(warmup_bars, len(klines) - 1))
        warm_ticks_by[sym] = ha.build_ticks(klines[:wb], symbol=sym)
        eval_ticks_by[sym] = ha.build_ticks(klines[wb:], symbol=sym)

    # Re-stamp each symbol's ticks inside its own slot of the bar so global
    # time stays monotonic across symbols: symbol j gets offsets
    # [j*10 + 0..3] seconds — every tick stays inside the same 15m bucket.
    SLOT_S = 10
    ordered_syms = sorted(eval_ticks_by)
    slot = {sym: j * SLOT_S for j, sym in enumerate(ordered_syms)}

    def _restamp(ticks: list[dict[str, Any]], sym: str) -> list[dict[str, Any]]:
        s = slot[sym]
        out = []
        for t in ticks:
            bar_open_ns = (t["ts_ns"] // (900 * _NS)) * (900 * _NS)
            off_s = (t["ts_ns"] // _NS) % 4  # 0..3 within the bar (build_ticks)
            t2 = dict(t)
            t2["ts_ns"] = bar_open_ns + (s + off_s) * _NS
            out.append(t2)
        return out

    for sym in ordered_syms:
        warm_ticks_by[sym] = _restamp(warm_ticks_by[sym], sym)
        eval_ticks_by[sym] = _restamp(eval_ticks_by[sym], sym)

    def _feed(ticks_by: dict[str, list[dict[str, Any]]], g_offset: int = 0):
        curve: list[tuple[int, float, float]] = []
        max_open = 0
        max_exp = 0.0
        prev_day = None
        n_bars = min(len(v) for v in ticks_by.values()) // 4

        def _on(t: dict[str, Any]) -> None:
            nonlocal max_open, max_exp, prev_day
            day = ha.utc_day(t["ts_ns"])
            if day != prev_day:
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

        for i in range(n_bars):
            _gate_note_bar(g_offset + i)
            for sym in ordered_syms:
                bar = ticks_by[sym][i * 4:(i + 1) * 4]
                _on(bar[0])
                pos = runner.portfolio.positions.get(bar[0]["symbol"])
                rest = bar[1:]
                if pos is not None and pos.side is PositionSide.SHORT and len(rest) == 3:
                    h, low = dict(bar[2]), dict(bar[1])
                    h["ts_ns"] = bar[0]["ts_ns"] + _NS
                    low["ts_ns"] = bar[0]["ts_ns"] + 2 * _NS
                    rest = [h, low, bar[3]]
                for t in rest:
                    _on(t)
        return curve, max_open, max_exp

    if warmup_bars:
        print(f"[entropy-multi] warmup: feeding {sum(len(v) for v in warm_ticks_by.values())} ticks ...")
        _feed(warm_ticks_by)
        warmup_trades = len(warm_ledger.fills) // 2
        if runner.portfolio.positions:
            first_ts = min(v[0]["ts_ns"] for v in eval_ticks_by.values())
            ha._liquidate_open_positions(runner, warm_ledger, first_ts,
                                         notify_reason="warmup_liquidation")
        runner.ledger = dummy  # type: ignore[assignment]
    else:
        warmup_trades = 0
        runner.ledger = dummy  # type: ignore[assignment]

    print(f"[entropy-multi] feeding {sum(len(v) for v in eval_ticks_by.values())} evaluated ticks ...")
    equity_curve, max_open, max_exposure_pct = _feed(eval_ticks_by, g_offset=warmup_bars)

    final_ts = max(v[-1]["ts_ns"] for v in eval_ticks_by.values())
    ha._liquidate_open_positions(runner, dummy, final_ts)

    snap = runner.portfolio.snapshot(final_ts)
    total_trades = len(dummy.fills) // 2
    costs_paid = sum(f.fee + f.slippage * f.qty for f, _ in dummy.fills)
    resolved = runner.executor.cost_model.for_symbol(ha.resolve_symbol(ordered_syms[0]))
    slip_bps = resolved.slippage_bps if resolved.slippage_bps is not None else cfg.slippage_bps
    fee_bps_close = resolved.fee_bps if resolved.fee_bps is not None else cfg.fee_bps

    wins = 0
    total_profit = total_loss = 0.0
    closed_pnls: list[float] = []
    trades: list[dict[str, Any]] = []
    positions_history: dict[str, Any] = {}
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
            if intent.value in ("stop", "take_profit"):
                exit_lv = dummy.exit_levels.pop(0) if dummy.exit_levels else None
                if exit_lv is not None:
                    levels = exit_lv
                if levels is not None:
                    stop_px, tp_px = levels
                    level = stop_px if intent.value == "stop" else tp_px
                    if fill.side.value == "sell":
                        exit_px = min(level * (1.0 - slip_bps / 10_000.0), fill.price)
                    else:
                        exit_px = max(level * (1.0 + slip_bps / 10_000.0), fill.price)
                    close_fee = abs(exit_px * fill.qty) * (fee_bps_close / 10_000.0)
            qty = fill.qty
            side_str = "LONG" if entry.side.value == "buy" else "SHORT"
            if side_str == "LONG":
                pnl = (exit_px - entry.price) * qty - entry.fee - close_fee
            else:
                pnl = (entry.price - exit_px) * qty - entry.fee - close_fee
            closed_pnls.append(pnl)
            trades.append({
                "symbol": fill.symbol,
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
            if pnl > 0:
                wins += 1
                total_profit += pnl
            else:
                total_loss += abs(pnl)

    win_rate = wins / total_trades if total_trades else 0.0
    profit_factor = total_profit / total_loss if total_loss > 0 else (total_profit if total_profit > 0 else 1.0)
    if len(closed_pnls) > 1:
        mean_pnl = sum(closed_pnls) / len(closed_pnls)
        var = sum((x - mean_pnl) ** 2 for x in closed_pnls) / (len(closed_pnls) - 1)
        sharpe = (mean_pnl / math.sqrt(var)) * math.sqrt(252) if var > 0 else 0.0
    else:
        sharpe = 0.0

    peak = -math.inf
    max_dd = 0.0
    for _, eq, _ in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0.0)

    by_symbol: dict[str, dict[str, Any]] = {}
    for t in trades:
        b = by_symbol.setdefault(t["symbol"], {"trades": 0, "wins": 0, "pnl": 0.0})
        b["trades"] += 1
        b["pnl"] = round(b["pnl"] + t["pnl"], 4)
        if t["pnl"] > 0:
            b["wins"] += 1

    final_equity = snap.equity
    total_return = (final_equity / cfg.starting_cash - 1.0) * 100.0
    report = {
        "metrics": {
            "final_equity": round(final_equity, 4),
            "total_return_pct": round(total_return, 4),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "sharpe": round(sharpe, 4),
            "costs_paid": round(costs_paid, 4),
            "max_drawdown_pct": round(max_dd * 100.0, 4),
            "max_exposure_pct_observed": round(max_exposure_pct * 100.0, 2),
            "max_concurrent_open": max_open,
            "halted": runner.risk.halted,
        },
        "by_symbol": by_symbol,
        "trades": trades,
        "exit_breakdown": dict(exit_breakdown),
        "avg_hold_bars": round(sum(hold_bars) / len(hold_bars), 2) if hold_bars else 0.0,
        "rejects": dict(Counter(r for _, r in dummy.rejects)),
        "warmup": {"bars": warmup_bars, "trades": warmup_trades},
        "gate": ({"bars": btc_gate_bars, "slope_bars": gate_slope_bars,
                  "stop_fuse": stop_fuse,
                  "entry_decisions": gate_eval_bars,
                  "entry_decisions_gated": gate_closed_bars,
                  "entry_decisions_fuse_blocked": fuse_active_bars}
                 if need_wrapper else None),
    }
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT")
    ap.add_argument("--bars", type=int, default=2880)
    ap.add_argument("--warmup-bars", type=int, default=100)
    ap.add_argument("--end-date", default="now")
    ap.add_argument("--out", default="/tmp/entropy_multi/run")
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
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--exit-mode", default="trail")
    ap.add_argument("--min-hold-bars", type=int, default=5)
    ap.add_argument("--cooldown-bars", type=int, default=4)
    ap.add_argument("--cost-edge-mult", type=float, default=1.0)
    ap.add_argument("--move-floor", type=float, default=0.0003)
    ap.add_argument("--vote-mode", default="trend")
    ap.add_argument("--normalize", default="total")
    ap.add_argument("--min-participation", type=float, default=0.5)
    ap.add_argument("--direction-bars", type=int, default=20)
    ap.add_argument("--confirm-bars", type=int, default=2)
    ap.add_argument("--trail-pct", type=float, default=0.3)
    ap.add_argument("--long-only", action="store_true", default=True)
    ap.add_argument("--allow-short", dest="long_only", action="store_false")
    ap.add_argument("--max-hold-bars", type=int, default=192)
    ap.add_argument("--stop-mode", choices=("percent", "sigma"), default="sigma")
    ap.add_argument("--stop-sigma-mult", type=float, default=20.0)
    ap.add_argument("--tp-sigma-mult", type=float, default=4.0)
    ap.add_argument("--entry-grace-bars", type=int, default=3)
    ap.add_argument("--risk-trail-pct", type=float, default=0.0)
    ap.add_argument("--btc-ema-gate", type=int, default=0,
                    help="pool-level entry kill-switch: no NEW entries while the "
                         "last completed BTC close < BTC EMA(n) on the same "
                         "timeframe (0 = off). Exits always pass. Pre-registered "
                         "v0 rule — do not tune N on the evaluation windows.")
    ap.add_argument("--gate-slope-bars", type=int, default=0,
                    help="v1: additionally require the EMA to have FALLEN over "
                         "this many bars for the gate to stay closed (0 = off, "
                         "v0 behaviour)")
    ap.add_argument("--gate-stop-fuse", default="",
                    help="v2: 'COUNT:WINDOW_BARS' — no new entries after COUNT "
                         "stop exits within the last WINDOW_BARS bars "
                         "(e.g. '3:192'; empty = off)")
    ap.add_argument("--skip-fetch", action="store_true")
    args = ap.parse_args()

    raws = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.btc_ema_gate > 0 and "BTCUSDT" not in raws:
        ap.error("--btc-ema-gate requires BTCUSDT in --symbols")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    end_ms = ha.parse_end(args.end_date)
    total_bars = args.bars + max(0, args.warmup_bars)

    per_symbol: dict[str, list[list[Any]]] = {}
    for raw in raws:
        cache = out_dir / f"klines-{raw}.json"
        klines = ha.fetch_klines(total_bars, end_ms, cache, raw=raw)
        per_symbol[raw] = klines[-total_bars:]
        print(f"[entropy-multi] {raw}: {len(klines)} bars "
              f"({datetime.fromtimestamp(klines[0][0]/1000, tz=timezone.utc):%Y-%m-%d %H:%M} -> "
              f"{datetime.fromtimestamp(klines[-1][6]/1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC)")
    n = min(len(v) for v in per_symbol.values())
    if any(len(v) < total_bars for v in per_symbol.values()):
        print(f"[entropy-multi] WARNING: trimming all symbols to common length {n}")
        per_symbol = {r: v[-n:] for r, v in per_symbol.items()}

    cfg = build_cfg(raws, args, out_dir)
    fuse = None
    if args.gate_stop_fuse:
        c, w = args.gate_stop_fuse.split(":")
        fuse = (int(c), int(w))
    report = simulate_multi(per_symbol, cfg, run_dir=str(out_dir / "ledger"),
                            warmup_bars=args.warmup_bars,
                            entry_grace_bars=args.entry_grace_bars, raws=raws,
                            btc_gate_bars=args.btc_ema_gate,
                            gate_slope_bars=args.gate_slope_bars,
                            stop_fuse=fuse)
    report["config"] = {
        "symbols": raws, "interval": ha.INTERVAL, "bars": args.bars,
        "warmup_bars": args.warmup_bars,
        "end_date": args.end_date, "starting_cash": args.cash,
        "btc_ema_gate": args.btc_ema_gate,
        "gate_slope_bars": args.gate_slope_bars,
        "gate_stop_fuse": args.gate_stop_fuse,
        "risk": {
            "per_trade_pct": args.per_trade_pct,
            "max_concurrent": args.max_concurrent,
            "max_total_exposure_pct": args.max_total_exposure_pct,
            "max_daily_loss_pct": args.max_daily_loss_pct,
            "stop_mode": args.stop_mode,
            "stop_sigma_mult": args.stop_sigma_mult,
            "tp_sigma_mult": args.tp_sigma_mult,
            "entry_grace_bars": args.entry_grace_bars,
            "risk_trail_pct": args.risk_trail_pct,
        },
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))

    m = report["metrics"]
    print(f"\n=== ENTROPY MULTI-SYMBOL — {','.join(raws)} 15m (real Binance data) ===")
    print(f"final equity : ${m['final_equity']:.2f}  (return {m['total_return_pct']:+.2f}%)")
    print(f"accuracy     : win rate {m['win_rate']*100:.1f}%  ({m['total_trades']} closed trades)")
    print(f"quality      : profit factor {m['profit_factor']:.2f}, sharpe {m['sharpe']:.2f}")
    print(f"risk         : max DD {m['max_drawdown_pct']:.2f}%, max exposure "
          f"{m['max_exposure_pct_observed']:.1f}%, max open {m['max_concurrent_open']}, halted={m['halted']}")
    print("by symbol    :")
    for sym, b in sorted(report["by_symbol"].items()):
        wr = b["wins"] / b["trades"] * 100 if b["trades"] else 0.0
        print(f"  {sym:28s} {b['trades']:4d}t  WR {wr:5.1f}%  pnl ${b['pnl']:+.2f}")
    print(f"exits        : {report['exit_breakdown']}  (avg hold {report['avg_hold_bars']} bars)")
    if report["gate"]:
        g = report["gate"]
        pct = (g["entry_decisions_gated"] / g["entry_decisions"] * 100.0
               if g["entry_decisions"] else 0.0)
        fpct = (g["entry_decisions_fuse_blocked"] / g["entry_decisions"] * 100.0
                if g["entry_decisions"] else 0.0)
        print(f"gate         : EMA({g['bars']}) slope={g['slope_bars']} "
              f"fuse={g['stop_fuse']} — {g['entry_decisions_gated']}/"
              f"{g['entry_decisions']} gated ({pct:.1f}%), fuse-blocked "
              f"{g['entry_decisions_fuse_blocked']} ({fpct:.1f}%)")
    print(f"report       : {out_dir / 'report.json'}")


if __name__ == "__main__":
    main()
