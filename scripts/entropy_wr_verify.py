#!/usr/bin/env python3
"""Verify win-rate candidate configs on out-of-sample windows.

For each candidate (passed as comma-separated keys or a CSV with a 'key'
column) runs the final-state BotRunner with Binance spot costs on:
  - recent 30d window (last 2880 bars)
  - the 4 walk-forward test folds (15d each, train=4320 offset)
and writes per-window metrics + chained OOS return.

Usage:
  .venv/bin/python scripts/entropy_wr_verify.py --klines /tmp/entropy_accuracy/120d_btc/klines.json \
      --keys "exit_mode=trend_flip/.../tp=0.5" --out /tmp/entropy_accuracy/wr/verify.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from entropy.bot.config import BotConfig, ConsensusConfig, MarketCostConfig, RiskOverrides  # noqa: E402
from entropy_accuracy_btc15m import SYMBOL, build_ticks, simulate  # noqa: E402
from entropy_wr_sweep import FIXED, build_cfg  # noqa: E402

FIELDS = ("exit_mode", "trail_pct", "threshold", "confirm_bars",
          "direction_bars", "min_hold_bars", "cooldown_bars", "sl", "tp")


def parse_key(key: str) -> dict[str, Any]:
    c: dict[str, Any] = dict(FIXED)
    for part in key.split("/"):
        k, v = part.split("=", 1)
        if k in ("exit_mode",):
            c[k] = v
        else:
            c[k] = float(v) if "." in v else int(v)
    return c


def windows(klines: list[list[Any]]) -> list[tuple[str, list[list[Any]]]]:
    out = [("30d", klines[-2880:])]
    for fold in range(4):
        start = 4320 + fold * 1440
        out.append((f"fold{fold}", klines[start: start + 1440]))
    return out


def run(klines: list[list[Any]], cfg: BotConfig) -> dict[str, Any]:
    return simulate(klines, cfg, run_dir=f"/tmp/entropy_accuracy/_wr/ledger-{os.getpid()}",
                    trade_csv=f"/tmp/entropy_accuracy/_wr/trades-{os.getpid()}.csv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klines", required=True)
    ap.add_argument("--keys", required=True, help="comma-separated config keys")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    klines = json.loads(Path(args.klines).read_text())["klines"]
    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    win = windows(klines)

    rows: list[dict[str, Any]] = []
    for key in keys:
        cfg = build_cfg(parse_key(key))
        for wname, wkl in win:
            r = run(wkl, cfg)
            m = r["metrics"]
            rows.append({
                "key": key, "window": wname,
                "return_pct": m["total_return_pct"], "trades": m["total_trades"],
                "win_rate": m["win_rate"], "pf": m["profit_factor"],
                "max_dd_pct": m["max_drawdown_pct"],
            })
        # chained OOS across the 4 folds (test slices), 30d excluded
        chain = 1.0
        for wname, _ in win:
            if wname.startswith("fold"):
                row = next(r for r in rows if r["key"] == key and r["window"] == wname)
                chain *= 1 + float(row["return_pct"]) / 100.0
        rows.append({"key": key, "window": "chained_oos",
                     "return_pct": (chain - 1) * 100, "trades": "",
                     "win_rate": "", "pf": "", "max_dd_pct": ""})
        print(f"[verify] {key.split('sl=')[0]}... 30d WR "
              f"{next(r['win_rate'] for r in rows if r['key']==key and r['window']=='30d')*100:.1f}% | "
              f"chained OOS {(chain-1)*100:+.2f}%", flush=True)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[verify] -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
