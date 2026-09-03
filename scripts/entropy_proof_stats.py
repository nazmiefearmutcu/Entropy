#!/usr/bin/env python3
"""Bootstrap / Wilson evidence stats for multi-symbol Entropy reports.

Reads one or more report.json files from entropy_multi_symbol.py runs and
prints:
  * Wilson 95% CI on win rate
  * iid bootstrap 95% CI on per-trade edge (% of position notional)
  * return CI via resampling DAILY realized-pnl returns reconstructed from the
    trade list (exit-timestamp order, pnl over running equity) — this captures
    the concurrency + compounding path the sequential-edge formula misses.

Usage: python entropy_proof_stats.py report1.json [report2.json ...]
"""
from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timezone

BOOT = 5000


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def pct_ci(sorted_vals: list[float]) -> tuple[float, float]:
    m = len(sorted_vals)
    return (sorted_vals[int(0.025 * m)], sorted_vals[min(m - 1, int(0.975 * m))])


def daily_returns(trades: list[dict], starting_cash: float) -> list[float]:
    """Realized-pnl daily returns in exit-timestamp order (approximation of the
    sim's intraday equity path; close enough for daily aggregation)."""
    if not trades:
        return []
    ts = sorted(t["exit_ts"] for t in trades)
    day_of = {t: datetime.fromisoformat(t).astimezone(timezone.utc).date() for t in ts}
    order = {t: i for i, t in enumerate(ts)}
    days: list[str] = []
    for t in ts:
        d = str(day_of[t])
        if not days or days[-1] != d:
            days.append(d)
    day_pnl = {d: 0.0 for d in days}
    equity = starting_cash
    eq_at_day_start: dict[str, float] = {days[0]: equity}
    cur_day = days[0]
    for t in sorted(trades, key=lambda x: order[x["exit_ts"]]):
        d = str(day_of[t["exit_ts"]])
        if d != cur_day:
            eq_at_day_start.setdefault(d, equity)
            cur_day = d
        equity += t["pnl"]
        day_pnl[d] += t["pnl"]
    rets = []
    for d in days:
        base = eq_at_day_start.get(d, equity)
        if base > 0:
            rets.append(day_pnl[d] / base * 100.0)
    return rets


def main() -> None:
    rng = random.Random(20260904)
    for path in sys.argv[1:]:
        r = json.load(open(path))
        m = r["metrics"]
        cfg = r.get("config", {})
        trades = r["trades"]
        n = len(trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        edges = [t["pnl_pct"] for t in trades]

        lo, hi = wilson(wins, n)
        boots_edge = []
        for _ in range(BOOT):
            s = [edges[rng.randrange(n)] for _ in range(n)]
            boots_edge.append(sum(s) / n)
        boots_edge.sort()
        e_lo, e_hi = pct_ci(boots_edge)

        rets = daily_returns(trades, cfg.get("starting_cash", 100.0))
        nd = len(rets)
        boots_ret = []
        for _ in range(BOOT):
            growth = 1.0
            for _ in range(nd):
                growth *= 1.0 + rets[rng.randrange(nd)] / 100.0
            boots_ret.append((growth - 1.0) * 100.0)
        boots_ret.sort()
        r_lo, r_hi = pct_ci(boots_ret)

        print(f"=== {path.split('/')[-1].split(chr(92))[-1]}  ({path})")
        print(f"  window   : {cfg.get('bars')} bars, end={cfg.get('end_date', 'now')}, "
              f"symbols={len(cfg.get('symbols', []))}")
        print(f"  return   : {m['total_return_pct']:+.2f}%  (per-trade "
              f"{cfg.get('risk', {}).get('per_trade_pct', 15.0)}%, "
              f"maxDD {m['max_drawdown_pct']:.2f}%, PF {m['profit_factor']}, sharpe {m['sharpe']})")
        print(f"  trades   : {n}, WR {m['win_rate']*100:.1f}%  Wilson95 [{lo*100:.1f}%, {hi*100:.1f}%]")
        mean_edge = sum(edges) / n
        print(f"  edge     : mean {mean_edge:+.3f}% of notional  boot95 [{e_lo:+.3f}%, {e_hi:+.3f}%]")
        print(f"  daily    : {nd} days; boot95 monthly-equivalent return CI "
              f"[{r_lo:+.2f}%, {r_hi:+.2f}%]  (point {m['total_return_pct']:+.2f}%)")
        print()


if __name__ == "__main__":
    main()
