#!/usr/bin/env python3
"""Rolling win-rate gate for the Entropy consensus bot.

Re-runs the ship-default accuracy gate (scripts/entropy_accuracy_btc15m.py
simulate()/fetch_klines, defaults = the verified "H" configuration) on the
rolling 30d window of real Binance BTCUSDT 15m bars ending now (or --end-date),
then applies the user's standing "keep win rate > 60% net of commissions"
directive as a hard predicate:

    PASS  iff  win_rate > 0.60  AND  trades >= 20
               AND  profit_factor >= 1.0  AND  total_return_pct >= 0

Prints PASS/FAIL, the four numbers and the Wilson 95% confidence interval for
the win rate (closed form, no scipy). Exit code 0 on PASS, 1 on FAIL, so it
can be scheduled (cron / Task Scheduler) as a continuous verification gate.

Usage:
  .venv/Scripts/python scripts/entropy_wr_gate.py
  .venv/Scripts/python scripts/entropy_wr_gate.py --bars 2880 --end-date 2026-09-03T00:00:00Z --out /tmp/entropy_wr_gate
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running straight from a checkout without an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# The script's own directory is sys.path[0] when run as a script; tests that
# import this file via importlib must register entropy_accuracy_btc15m first.
import entropy_accuracy_btc15m as acc
from entropy_accuracy_btc15m import fetch_klines, simulate  # noqa: F401

from entropy.bot.config import (  # noqa: E402
    BotConfig,
    ConsensusConfig,
    MarketCostConfig,
    RiskOverrides,
)

# ---- gate thresholds (the standing directive, as hard numbers) -------------
MIN_WIN_RATE = 0.60
MIN_TRADES = 20
MIN_PROFIT_FACTOR = 1.0
MIN_RETURN_PCT = 0.0

WILSON_Z = 1.96  # 95% two-sided


def wilson_interval(wins: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (closed form).

    (p + z^2/2n +- z*sqrt(p(1-p)/n + z^2/4n^2)) / (1 + z^2/n), clamped to
    [0, 1]. With n == 0 there is no information: the interval is [0, 1].
    """
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def gate_verdict(win_rate: float, trades: int, profit_factor: float,
                 total_return_pct: float) -> tuple[bool, list[str]]:
    """The gate predicate: (passed, list of failed criterion strings)."""
    failed: list[str] = []
    if not win_rate > MIN_WIN_RATE:
        failed.append(f"win_rate {win_rate:.4f} <= {MIN_WIN_RATE}")
    if not trades >= MIN_TRADES:
        failed.append(f"trades {trades} < {MIN_TRADES}")
    if not profit_factor >= MIN_PROFIT_FACTOR:
        failed.append(f"profit_factor {profit_factor:.4f} < {MIN_PROFIT_FACTOR}")
    if not total_return_pct >= MIN_RETURN_PCT:
        failed.append(f"total_return_pct {total_return_pct:.4f} < {MIN_RETURN_PCT}")
    return (not failed, failed)


def build_ship_default_cfg(cash: float, symbol: str, out_dir: Path, *,
                         vote_mode: str = "adaptive",
                         risk_trail_pct: float = 0.0) -> BotConfig:
    """The exact configuration scripts/entropy_accuracy_btc15m.py builds from
    its CLI defaults — which since 2026-09-03 ARE the shipped "H" config.
    Kept 1:1 with that script so the gate measures precisely what the
    scheduled accuracy runs measure (see the T5 brief / PROJECT.md).

    Round-2 levers (T9a — gate parity with what T9 ships):
      * vote_mode -> ConsensusConfig.vote_mode (the gate runs one symbol at
        a time like the harness, so a single mode per run).
      * risk_trail_pct -> RiskOverrides.risk_trail_pct (0.0 = off).
      * entry_grace_bars is NOT a cfg field: simulate() takes it as a
        top-level kwarg (see entropy_accuracy_btc15m.simulate), so it is
        forwarded at the simulate() call site in main(), not here.
    With all three at their defaults the returned cfg is field-identical to
    the harness's CLI-default cfg."""
    return BotConfig(
        mode="paper",
        starting_cash=cash,
        strategies=("consensus",),
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
        cost_edge_mult=1.0,
        consensus=ConsensusConfig(
            threshold=0.5,
            exit_mode="trail",
            min_hold_bars=5,
            cooldown_bars=4,
            move_floor=0.0003,
            vote_mode=vote_mode,
            normalize="total",
            min_participation=0.5,
            direction_bars=20,
            confirm_bars=2,
            trail_pct=0.3,
            max_hold_bars=96,
            long_only=True,
        ),
        risk_overrides=RiskOverrides(
            per_trade_pct=10.0,
            max_concurrent=4,
            stop_loss_pct=1.5,
            take_profit_pct=1.2,
            max_total_exposure_pct=40.0,
            max_daily_loss_pct=40.0,
            cooldown_s=180.0,
            min_volatility_pct=0.05,
            vol_window_s=900.0,
            stop_mode="sigma",
            stop_sigma_mult=5.0,
            tp_sigma_mult=4.0,
            risk_trail_pct=risk_trail_pct,
        ),
        console_log_path=str(out_dir / "console.log"),
        trade_csv_path=str(out_dir / "trades.csv"),
    )


def wins_of(report: dict[str, Any]) -> int:
    """Exact win count (pnl > 0) from the report's per-trade list — the
    metrics block carries only the rounded win RATE, and the Wilson interval
    must be built on the true integer win count."""
    return sum(1 for t in report["trades"] if t["pnl"] > 0)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rolling win-rate gate: ship defaults on the last N 15m "
                    "bars; PASS iff WR > 0.60, trades >= 20, PF >= 1.0, "
                    "return >= 0. Exit 0/1.")
    ap.add_argument("--bars", type=int, default=2880,
                    help="evaluated 15m bars in the rolling window "
                         "(2880 = 30 days)")
    ap.add_argument("--warmup-bars", type=int, default=100,
                    help="extra state-seeding bars before the window "
                         "(their trades are not counted)")
    ap.add_argument("--symbol", default="BTCUSDT",
                    help="Binance spot symbol to gate on")
    ap.add_argument("--end-date", default="now",
                    help="window end ('now' or ISO-8601, e.g. 2026-09-03T00:00:00Z)")
    ap.add_argument("--cash", type=float, default=100.0)
    ap.add_argument("--vote-mode", default="adaptive",
                    choices=("adaptive", "trend", "mean_revert", "legacy"),
                    help="consensus vote mode for this gate run (single mode "
                         "per run — the gate gates one symbol at a time)")
    ap.add_argument("--risk-trail-pct", type=float, default=0.0,
                    help="risk-trail stop ratchet as a fraction of the TP "
                         "distance at open (0 = off)")
    ap.add_argument("--entry-grace-bars", type=int, default=0,
                    help="suppress the MECHANICAL stop for the entry bar + "
                         "N-1 following completed bars of each position "
                         "(0 = off; forwarded to simulate(), not via cfg)")
    ap.add_argument("--out", default="/tmp/entropy_wr_gate",
                    help="output dir (klines cache + report.json land here)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    end_ms = acc.parse_end(args.end_date)
    raw = args.symbol.upper()
    symbol = acc.resolve_symbol(args.symbol)
    print(f"[wr-gate] fetching {args.bars + args.warmup_bars} x 15m {raw} bars "
          f"ending {end_ms} ...")
    klines = fetch_klines(args.bars + args.warmup_bars, end_ms,
                          out_dir / "klines.json", raw=raw)
    if not klines:
        print("[wr-gate] FAIL: no klines fetched")
        return 1

    cfg = build_ship_default_cfg(args.cash, symbol, out_dir,
                                   vote_mode=args.vote_mode,
                                   risk_trail_pct=args.risk_trail_pct)
    report = simulate(klines, cfg, run_dir=str(out_dir / "ledger"),
                      trade_csv=str(out_dir / "trades.csv"), symbol=symbol,
                      warmup_bars=args.warmup_bars,
                      entry_grace_bars=args.entry_grace_bars)
    m = report["metrics"]

    win_rate = m["win_rate"]
    trades = m["total_trades"]
    pf = m["profit_factor"]
    ret = m["total_return_pct"]
    lo, hi = wilson_interval(wins_of(report), trades)

    passed, failed = gate_verdict(win_rate, trades, pf, ret)
    verdict = "PASS" if passed else "FAIL"
    end_utc = datetime.fromtimestamp(klines[-1][6] / 1000, tz=timezone.utc)
    print(f"\n=== ENTROPY WR GATE — {raw} 15m, rolling {args.bars} bars "
          f"(~{args.bars / 96:.0f}d) ending {end_utc:%Y-%m-%d %H:%M} UTC ===")
    print(f"win_rate         : {win_rate * 100:.1f}%  "
          f"(Wilson 95% CI [{lo * 100:.1f}%, {hi * 100:.1f}%])")
    print(f"trades           : {trades}")
    print(f"profit_factor    : {pf:.2f}")
    print(f"total_return_pct : {ret:+.2f}%")
    print(f"levers           : vote_mode={args.vote_mode}, "
          f"risk_trail_pct={args.risk_trail_pct:g}, "
          f"entry_grace_bars={args.entry_grace_bars}")
    print(f"thresholds       : WR > {MIN_WIN_RATE:.2f}, trades >= {MIN_TRADES}, "
          f"PF >= {MIN_PROFIT_FACTOR:.2f}, return >= {MIN_RETURN_PCT:.2f}%")
    for reason in failed:
        print(f"failed           : {reason}")
    print(f"verdict          : {verdict}")

    (out_dir / "report.json").write_text(json.dumps({
        "gate": {
            "verdict": verdict,
            "min_win_rate": MIN_WIN_RATE,
            "min_trades": MIN_TRADES,
            "min_profit_factor": MIN_PROFIT_FACTOR,
            "min_return_pct": MIN_RETURN_PCT,
            "failed": failed,
            "wilson_95ci": [round(lo, 4), round(hi, 4)],
        },
        "metrics": m,
        "config": {
            "symbol": symbol,
            "bars": args.bars,
            "warmup_bars": args.warmup_bars,
            "end_ms": end_ms,
            "vote_mode": args.vote_mode,
            "risk_trail_pct": args.risk_trail_pct,
            "entry_grace_bars": args.entry_grace_bars,
        },
        "exit_breakdown": report["exit_breakdown"],
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
