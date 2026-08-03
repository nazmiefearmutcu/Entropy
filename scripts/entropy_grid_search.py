#!/usr/bin/env python3
"""Walk-forward grid search for the Entropy accuracy test.

Sweeps consensus-strategy knobs + risk SL/TP on a TRAIN slice of real BTCUSDT
15m bars, using the same $100 / Binance-spot-cost / 15m setup as
entropy_accuracy_btc15m.py. Supports sharding so parallel agents can each
sweep a slice:

    .venv/bin/python scripts/entropy_grid_search.py --klines /tmp/entropy_accuracy/30d/klines.json \
        --train-bars 2000 --shard 0 --shard-total 4 --out /tmp/entropy_accuracy/grid_0.csv

Ranking score = net return % on the train slice (>= 20 trades required);
every combo's win_rate / pf / max_dd / costs are recorded for later OOS checks.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from entropy.bot.config import BotConfig, ConsensusConfig, MarketCostConfig, RiskOverrides  # noqa: E402
from entropy_accuracy_btc15m import SYMBOL, build_ticks, simulate  # noqa: E402

GRID = {
    "threshold": (0.5, 0.65),
    "exit_mode": ("score", "trend_flip"),
    "min_hold_bars": (5, 8),
    "vote_mode": ("adaptive",),
    "normalize": ("participating", "total"),
    "min_participation": (0.5,),
    "direction_bars": (0, 20),
    "confirm_bars": (1, 2),
    "sl": (1.0, 1.5),
    "tp": (2.0, 3.0),
    "cost_edge_mult": (1.0,),
    "cooldown_bars": (2, 4),
}

KEYS = tuple(GRID.keys())


def combos() -> list[dict[str, Any]]:
    out = []
    for values in itertools.product(*(GRID[k] for k in KEYS)):
        d = dict(zip(KEYS, values, strict=True))
        if d["tp"] <= d["sl"]:
            continue  # keep payoff > 1
        out.append(d)
    return out


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
            move_floor=0.0003,
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
        console_log_path="/tmp/entropy_accuracy/_grid/console.log",
        trade_csv_path="/tmp/entropy_accuracy/_grid/trades.csv",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klines", required=True)
    ap.add_argument("--train-bars", type=int, default=2000)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shard-total", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import json
    klines = json.loads(Path(args.klines).read_text())["klines"][: args.train_bars]
    ticks = build_ticks(klines)
    all_combos = combos()
    mine = all_combos[args.shard :: args.shard_total]
    print(f"[grid] {len(mine)} combos (shard {args.shard}/{args.shard_total}) "
          f"on {len(ticks)} ticks", flush=True)

    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for i, c in enumerate(mine, 1):
        cfg = build_cfg(c)
        rep = simulate(klines, cfg)
        m = rep["metrics"]
        row = {**c, "trades": m["total_trades"], "win_rate": m["win_rate"],
               "net_return": m["total_return_pct"], "pf": m["profit_factor"],
               "max_dd": m["max_drawdown_pct"], "costs": m["costs_paid"],
               "final_equity": m["final_equity"]}
        rows.append(row)
        if i % 25 == 0 or i == len(mine):
            el = time.perf_counter() - t0
            print(f"[grid] {i}/{len(mine)} elapsed {el:.0f}s", flush=True)

    rows.sort(key=lambda r: (-r["net_return"] if r["trades"] >= 20 else 1e9, -r["win_rate"]))
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[grid] done -> {args.out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
