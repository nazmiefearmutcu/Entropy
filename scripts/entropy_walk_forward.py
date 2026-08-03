#!/usr/bin/env python3
"""Walk-forward validation for the Entropy consensus strategy on real 15m bars.

Design: rolling folds. For each fold we sweep a candidate set on the TRAIN
slice (rank by net return, >= 20 trades), then evaluate the best config on the
following TEST slice. The fixed "ship default" config is always evaluated on
the test slice too, plus HODL of the underlying for reference.

Default layout on 120d BTCUSDT (11519 bars):
  train = 4320 bars (45d), test = 1440 bars (15d), step = 15d -> 4 folds.

Usage:
  .venv/bin/python scripts/entropy_walk_forward.py --klines /tmp/entropy_accuracy/120d_btc/klines.json \
      --out /tmp/entropy_accuracy/wf/fold0 --fold 0
  ... (fold 0..3; then combine the oos.csv files)

Outputs per --out dir:
  sweep.csv  all combos with train metrics (ranked by return)
  oos.csv    test-slice metrics for best-on-train + ship default + HODL
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from entropy.bot.config import BotConfig, ConsensusConfig, MarketCostConfig, RiskOverrides  # noqa: E402
from entropy_accuracy_btc15m import SYMBOL, build_ticks, simulate  # noqa: E402

# Candidate sweep: only the knobs that materially changed behaviour in the
# 512-combo grid; SL/TP fixed at the ship defaults (1.5 / 2.0) so the payoff
# ratio stays constant and runtime stays bounded. 2^5 = 32 combos.
SWEEP = {
    "exit_mode": ("trend_flip", "score"),
    "min_hold_bars": (5, 8),
    "cooldown_bars": (2, 4),
    "direction_bars": (0, 20),
    "confirm_bars": (1, 2),
}
FIXED = {
    "threshold": 0.5,
    "vote_mode": "adaptive",
    "normalize": "total",
    "min_participation": 0.5,
    "move_floor": 0.0003,
    "sl": 1.5,
    "tp": 2.0,
    "cost_edge_mult": 1.0,
}

DEFAULT = {"exit_mode": "trend_flip", "min_hold_bars": 8, "cooldown_bars": 4,
           "direction_bars": 20, "confirm_bars": 2}


def combos() -> list[dict[str, Any]]:
    out = []
    for values in itertools.product(*(SWEEP[k] for k in SWEEP)):
        d = dict(zip(SWEEP.keys(), values, strict=True))
        d.update(FIXED)
        out.append(d)
    return out


def combo_key(c: dict[str, Any]) -> str:
    return "/".join(f"{k}={c[k]}" for k in (*SWEEP, "sl", "tp"))


def build_cfg(c: dict[str, Any]) -> BotConfig:
    return BotConfig(
        mode="paper",
        starting_cash=100.0,
        strategies=("consensus",),
        symbols=(SYMBOL,),
        ema_symbol=SYMBOL,
        timeframe="15m",
        bar_s=900.0,
        warmup=False,
        market_costs=MarketCostConfig(),
        cost_aware=True,
        cost_edge_mult=c["cost_edge_mult"],
        consensus=ConsensusConfig(
            threshold=c["threshold"],
            exit_mode=c["exit_mode"],
            min_hold_bars=c["min_hold_bars"],
            cooldown_bars=c["cooldown_bars"],
            move_floor=c["move_floor"],
            vote_mode=c["vote_mode"],
            normalize=c["normalize"],
            min_participation=c["min_participation"],
            direction_bars=c["direction_bars"],
            confirm_bars=c["confirm_bars"],
        ),
        risk_overrides=RiskOverrides(
            per_trade_pct=10.0,
            max_concurrent=4,
            stop_loss_pct=c["sl"],
            take_profit_pct=c["tp"],
            max_total_exposure_pct=40.0,
            max_daily_loss_pct=40.0,
            cooldown_s=180.0,
            min_volatility_pct=0.05,
            vol_window_s=900.0,
        ),
        console_log_path=f"/tmp/entropy_accuracy/_wf/console-{os.getpid()}.log",
        trade_csv_path=f"/tmp/entropy_accuracy/_wf/trades-{os.getpid()}.csv",
    )


def metrics(r: dict[str, Any]) -> dict[str, float | int]:
    m = r["metrics"]
    return {
        "return_pct": m["total_return_pct"],
        "trades": m["total_trades"],
        "win_rate": m["win_rate"],
        "pf": m["profit_factor"],
        "max_dd_pct": m["max_drawdown_pct"],
        "costs": m["costs_paid"],
    }


def run(klines: list[list[Any]], cfg: BotConfig, tag: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    report = simulate(klines, cfg, run_dir=f"/tmp/entropy_accuracy/_wf/ledger-{os.getpid()}",
                      trade_csv=f"/tmp/entropy_accuracy/_wf/trades-{os.getpid()}.csv")
    print(f"[wf:{tag}] {time.perf_counter()-t0:.1f}s -> {report['metrics']['total_return_pct']:+.2f}% "
          f"({report['metrics']['total_trades']} trades)", flush=True)
    return report


def hodl_return(klines: list[list[Any]]) -> float:
    return (float(klines[-1][4]) / float(klines[0][1]) - 1.0) * 100.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klines", required=True, help="json with 'klines' list of raw Binance bars")
    ap.add_argument("--train-bars", type=int, default=4320)
    ap.add_argument("--test-bars", type=int, default=1440)
    ap.add_argument("--fold", type=int, default=None, help="run one fold only (default: all)")
    ap.add_argument("--out", required=True, help="output dir (or prefix when running all folds)")
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="debug: only first N combos")
    args = ap.parse_args()

    klines = json.loads(Path(args.klines).read_text())["klines"]
    n = len(klines)
    n_folds = (n - args.train_bars) // args.test_bars
    folds = [args.fold] if args.fold is not None else list(range(n_folds))
    print(f"[wf] {n} bars, {n_folds} folds, train={args.train_bars} test={args.test_bars} "
          f"-> running folds {folds}", flush=True)

    all_combos = combos()[: args.limit] if args.limit else combos()
    default_cfg = build_cfg({**FIXED, **DEFAULT})

    for fold in folds:
        t_start = fold * args.test_bars
        train = klines[t_start: t_start + args.train_bars]
        test = klines[t_start + args.train_bars: t_start + args.train_bars + args.test_bars]
        if len(test) < args.test_bars:
            print(f"[wf] fold {fold}: skipping, short test slice ({len(test)} bars)", flush=True)
            continue
        out_dir = Path(args.out) if args.fold is not None else Path(f"{args.out}_fold{fold}")
        out_dir.mkdir(parents=True, exist_ok=True)

        rows: list[dict[str, Any]] = []
        for c in all_combos:
            r = run(train, build_cfg(c), f"fold{fold}-train")
            rows.append({**metrics(r), "key": combo_key(c), "_c": c})
        rows.sort(key=lambda x: -float(x["return_pct"]))
        out_rows = [{k: v for k, v in r.items() if k != "_c"} for r in rows]
        with (out_dir / "sweep.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)

        eligible = [r for r in rows if int(r["trades"]) >= args.min_trades]
        best = eligible[0] if eligible else rows[0]
        best_cfg = best["_c"]

        oos: list[dict[str, Any]] = []
        for label, cfg in (("best_on_train", build_cfg(best_cfg)), ("ship_default", default_cfg)):
            r = run(test, cfg, f"fold{fold}-test-{label}")
            oos.append({"fold": fold, "config": label, "key": combo_key(
                {**FIXED, **DEFAULT} if label == "ship_default" else best_cfg),
                **metrics(r), "hodl_return_pct": round(hodl_return(test), 4)})
        with (out_dir / "oos.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(oos[0].keys()))
            w.writeheader()
            w.writerows(oos)

        print(f"[wf] fold {fold}: best_train={best['key']} ({best['return_pct']:+.2f}%) -> "
              f"OOS {oos[0]['return_pct']:+.2f}%; ship_default OOS {oos[1]['return_pct']:+.2f}%; "
              f"HODL {oos[0]['hodl_return_pct']:+.2f}%", flush=True)
        print(f"[wf] fold {fold} -> {out_dir}/sweep.csv, {out_dir}/oos.csv", flush=True)


if __name__ == "__main__":
    main()
