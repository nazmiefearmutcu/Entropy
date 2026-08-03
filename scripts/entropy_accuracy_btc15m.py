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
from entropy.bot.orders import OrderSide
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
    ticks: list[dict[str, Any]] = []
    for k in klines:
        open_ms = int(k[0])
        open_px, high, low, close = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        # open, high, low, close as 4 ticks inside the bar; strategy bars bucket
        # on ts//900s so all four land in the same 15m bar.
        for off_s, px in ((0, open_px), (1, high), (2, low), (3, close)):
            ticks.append({
                "symbol": symbol, "price": px, "amount": 1.0, "side": "buy",
                "ts_ns": (open_ms + off_s * 1000) * 1_000_000,
            })
    return ticks


def utc_day(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / _NS, tz=timezone.utc).strftime("%Y-%m-%d")


def simulate(klines: list[list[Any]], cfg: BotConfig,
             run_dir: str = "/tmp/entropy_accuracy/_sim/ledger",
             trade_csv: str = "/tmp/entropy_accuracy/_sim/trades.csv",
             symbol: str = SYMBOL) -> dict[str, Any]:
    """Feed real 15m klines through the final-state BotRunner and compute the
    full accuracy report. Mirrors the metric math of calibration.run_backtest
    (end-of-test liquidation, paired fills, costs)."""
    out_dir = Path(run_dir).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = BotRunner(cfg, run_dir=str(out_dir / "ledger"))
    dummy = DummyLedger()
    runner.ledger = dummy  # type: ignore[assignment]

    ticks = build_ticks(klines, symbol=symbol)
    equity_curve: list[tuple[int, float, float]] = []  # (ts_ns, equity, exposure)
    max_exposure_pct = 0.0
    max_open = 0
    prev_day = None
    print(f"[entropy-accuracy] feeding {len(ticks)} ticks ...")
    for t in ticks:
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
        equity_curve.append((t["ts_ns"], snap.equity, snap.open_count))
        if snap.open_count > max_open:
            max_open = snap.open_count
        exp = runner.portfolio.exposure()
        if snap.equity > 0 and exp / snap.equity > max_exposure_pct:
            max_exposure_pct = exp / snap.equity

    # ---- end-of-test liquidation (mirrors calibration.run_backtest) ----
    final_ts = ticks[-1]["ts_ns"]
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
        from entropy.bot.orders import Fill, OrderIntent
        dummy.record_fill(
            Fill(order_id=f"liq-{symbol}", symbol=symbol, side=close_side, qty=pos.qty,
                 price=fill_px, fee=fee, slippage=slip, ts_ns=final_ts),
            OrderIntent.CLOSE,
        )
        runner.portfolio.close(symbol, fill_px, final_ts, fee=fee)

    snap = runner.portfolio.snapshot(final_ts)
    total_trades = len(dummy.fills) // 2
    costs_paid = sum(f.fee + f.slippage * f.qty for f, _ in dummy.fills)
    wins = losses = 0
    total_profit = total_loss = 0.0
    closed_pnls: list[float] = []
    trades: list[dict[str, Any]] = []
    positions_history: dict[str, Any] = {}
    exit_breakdown: Counter = Counter()
    hold_bars: list[float] = []
    for fill, intent in dummy.fills:
        if intent.value == "open":
            positions_history[fill.symbol] = fill
        else:
            entry = positions_history.pop(fill.symbol)
            qty = fill.qty
            if entry.side.value == "buy":
                pnl = (fill.price - entry.price) * qty - entry.fee - fill.fee
            else:
                pnl = (entry.price - fill.price) * qty - entry.fee - fill.fee
            closed_pnls.append(pnl)
            trades.append({
                "side": "LONG" if entry.side.value == "buy" else "SHORT",
                "entry_px": round(entry.price, 2), "exit_px": round(fill.price, 2),
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
                losses += 1
                total_loss += abs(pnl)

    win_rate = wins / total_trades if total_trades else 0.0
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
    }
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=2880, help="number of 15m bars")
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
    ap.add_argument("--direction-bars", type=int, default=0)
    ap.add_argument("--confirm-bars", type=int, default=2)
    ap.add_argument("--trail-pct", type=float, default=0.3)
    ap.add_argument("--strategy", action="append", default=None,
                    help="strategy name (repeatable); default consensus")
    args = ap.parse_args()

    symbol = resolve_symbol(args.symbol)
    raw = args.symbol.upper()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    end_ms = parse_end(args.end_date)
    cache = None if args.skip_fetch else out_dir / "klines.json"
    print(f"[entropy-accuracy] fetching {args.bars} x 15m {raw} bars ending {end_ms} ...")
    klines = fetch_klines(args.bars, end_ms, cache, raw=raw)
    print(f"[entropy-accuracy] got {len(klines)} closed bars "
          f"({datetime.fromtimestamp(klines[0][0]/1000, tz=timezone.utc):%Y-%m-%d %H:%M} -> "
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
        ),
        console_log_path=str(out_dir / "console.log"),
        trade_csv_path=str(out_dir / "trades.csv"),
    )

    report = simulate(klines, cfg, run_dir=str(out_dir / "ledger"),
                      trade_csv=str(out_dir / "trades.csv"), symbol=symbol)
    report["config"] = {
        "symbol": symbol, "interval": INTERVAL, "bars": len(klines),
        "start_utc": datetime.fromtimestamp(klines[0][0] / 1000, tz=timezone.utc).isoformat(),
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
        },
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
    print(f"final equity : ${m['final_equity']:.2f}  (return {m['total_return_pct']:+.2f}%)")
    print(f"accuracy     : win rate {m['win_rate']*100:.1f}%  ({m['total_trades']} closed trades)")
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
