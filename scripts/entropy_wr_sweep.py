#!/usr/bin/env python3
"""Win-rate-focused sweep for the Entropy consensus strategy on real 15m bars.

Sweeps a WR-focused knob space (payoff asymmetry: TP < SL, exit modes,
direction/confirm filters) through the final-state BotRunner with Binance spot
costs (26 bps round trip), same setup as entropy_accuracy_btc15m.py. Writes a
CSV sorted by win rate (min-trades filter) and supports sharding for parallel
agents.

Usage:
  .venv/bin/python scripts/entropy_wr_sweep.py --klines /tmp/entropy_accuracy/120d_btc/klines.json \
      --bars 5760 --shard 0 --shard-total 8 --out /tmp/entropy_accuracy/wr/wave1_s0.csv

The space: 1152 base combos (exit trend_flip|score|either) + 192 trail combos
= 1344 total.
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

BASE = {
    "exit_mode": ("trend_flip", "score", "either"),
    "threshold": (0.5, 0.65),
    "confirm_bars": (1, 2),
    "direction_bars": (0, 20),
    "min_hold_bars": (3, 5, 8),
    "cooldown_bars": (2, 4),
    "sl": (1.5, 2.0),
    "tp": (0.5, 0.8, 1.2, 1.8),
}
TRAIL = {
    "exit_mode": ("trail",),
    "trail_pct": (0.3, 0.5, 0.8),
    "threshold": (0.5,),
    "confirm_bars": (1, 2),
    "direction_bars": (0, 20),
    "min_hold_bars": (5, 8),
    "cooldown_bars": (2, 4),
    "sl": (1.5, 2.0),
    "tp": (0.8, 1.2),
}

WAVE2 = {
    "exit_mode": ("trend_flip", "trail"),
    "trail_pct": (0.0, 0.5),
    "threshold": (0.5,),
    "confirm_bars": (1, 2),
    "direction_bars": (0, 20),
    "min_hold_bars": (3, 5, 8),
    "cooldown_bars": (2, 4),
    "sl": (1.5, 2.0),
    "tp": (0.5, 0.8),
}

FIXED = {"vote_mode": "adaptive", "normalize": "total",
         "min_participation": 0.5, "move_floor": 0.0003,
         "cost_edge_mult": 1.0}


def combos(space_name: str = "full") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    spaces = {"full": (BASE, TRAIL), "wave2": (WAVE2,)}[space_name]
    for space in spaces:
        for values in itertools.product(*(space[k] for k in space)):
            d = dict(zip(space.keys(), values, strict=True))
            if d["exit_mode"] != "trail":
                d["trail_pct"] = 0.0
            d.update(FIXED)
            out.append(d)
    return out


def combo_key(c: dict[str, Any]) -> str:
    return "/".join(f"{k}={c[k]}" for k in
                    ("exit_mode", "trail_pct", "threshold", "confirm_bars",
                     "direction_bars", "min_hold_bars", "cooldown_bars",
                     "sl", "tp"))


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
            trail_pct=c["trail_pct"],
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
        console_log_path=f"/tmp/entropy_accuracy/_wr/console-{os.getpid()}.log",
        trade_csv_path=f"/tmp/entropy_accuracy/_wr/trades-{os.getpid()}.csv",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klines", required=True)
    ap.add_argument("--bars", type=int, default=5760, help="train window bars from end")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shard-total", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="debug: only first N combos")
    ap.add_argument("--space", choices=("full", "wave2"), default="full")
    args = ap.parse_args()

    klines = json.loads(Path(args.klines).read_text())["klines"][-args.bars:]
    ticks = build_ticks(klines)
    all_combos = combos(args.space)[: args.limit] if args.limit else combos(args.space)
    mine = all_combos[args.shard:: args.shard_total]
    print(f"[wr] {len(mine)} combos (shard {args.shard}/{args.shard_total}) on {len(ticks)} ticks",
          flush=True)

    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for i, c in enumerate(mine, 1):
        r = simulate(klines, build_cfg(c), run_dir=f"/tmp/entropy_accuracy/_wr/ledger-{os.getpid()}",
                     trade_csv=f"/tmp/entropy_accuracy/_wr/trades-{os.getpid()}.csv")
        m = r["metrics"]
        rows.append({
            "key": combo_key(c),
            "return_pct": m["total_return_pct"],
            "trades": m["total_trades"],
            "win_rate": m["win_rate"],
            "pf": m["profit_factor"],
            "max_dd_pct": m["max_drawdown_pct"],
            "costs": m["costs_paid"],
            "avg_hold_bars": r["avg_hold_bars"],
            "exits": r["exit_breakdown"],
        })
        if i % 25 == 0:
            print(f"[wr] {i}/{len(mine)} done, {time.perf_counter()-t0:.0f}s", flush=True)

    eligible = [r for r in rows if int(r["trades"]) >= args.min_trades]
    ranked = sorted(eligible, key=lambda x: -float(x["win_rate"])) + \
        sorted((r for r in rows if r not in eligible), key=lambda x: -float(x["win_rate"]))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ranked[0].keys()))
        w.writeheader()
        w.writerows(ranked)
    print(f"[wr] done {time.perf_counter()-t0:.0f}s -> {args.out}", flush=True)
    if ranked:
        top = ranked[0]
        print(f"[wr] top: WR {float(top['win_rate'])*100:.1f}% ({top['trades']}t) "
              f"ret {top['return_pct']:+.2f}% PF {top['pf']:.2f} :: {top['key']}", flush=True)


if __name__ == "__main__":
    main()
