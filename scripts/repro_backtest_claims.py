"""Reproduce the calibration backtest claims from the cost-aware audit.

Runs two backtests on the same seeded tick stream:

* ``market_costs=None`` — legacy flat-fee path, every cost gate OFF
* ``market_costs=MarketCostConfig()`` — per-venue costs, cost gates ON

and prints ``final_equity`` / ``win_rate`` / ``profit_factor`` /
``total_trades`` / ``costs_paid`` for each. It also runs the audit's critical
control: FROSTY's 0.5% stop against Binance-spot costs (26 bps round trip)
trips the risk layer's cost-to-stop guard (0.52 > 0.5), so the cost-aware run
never trades at all — while the flat path still does.

Run from the repo root:

    .venv/bin/python scripts/repro_backtest_claims.py [--ticks 200] [--seed 42]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Allow running straight from a checkout without an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from entropy.bot.calibration import generate_ticks, run_backtest
from entropy.bot.config import MarketCostConfig

#: The audit compared these metrics between the gates-off and gates-on runs.
METRIC_KEYS = ("final_equity", "win_rate", "profit_factor", "total_trades", "costs_paid")


def format_result(label: str, res: dict[str, Any]) -> str:
    lines = [f"{label}"]
    for key in METRIC_KEYS:
        value = res[key]
        rendered = f"{value:g}" if isinstance(value, float) else f"{value}"
        lines.append(f"  {key} = {rendered}")
    return "\n".join(lines)


def run_case(
    label: str,
    ticks: list[dict[str, Any]],
    symbols: list[str],
    market_costs: MarketCostConfig | None,
    *,
    stop_loss_pct: float = 1.0,
) -> dict[str, Any]:
    """Run one backtest and print the audited metrics."""
    res = run_backtest(
        ticks,
        symbols,
        fast=9,
        slow=21,
        min_pct=0.15,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=2.0,
        market_costs=market_costs,
    )
    print(format_result(label, res))
    return res


def frosty_spot_control(n_ticks: int, seed: int) -> None:
    """FROSTY's 0.5% stop + Binance-spot costs must never trade.

    Spot round trip is 26 bps; 26 / 50 = 0.52 > max_cost_to_stop (0.5), so the
    risk layer rejects every entry. This is the audit's critical finding: with
    the cost-aware gates accidentally active in the flat path (pre-fix), even
    ``market_costs=None`` runs could be blocked; the flat path must trade.
    """
    ticks = generate_ticks(["SOLUSDT"], n_ticks, seed=seed)
    print("\nFROSTY+spot control (0.5% stop, take-profit 2.0%):")
    flat = run_case("flat path (market_costs=None)", ticks, ["SOLUSDT"], None,
                    stop_loss_pct=0.5)
    gated = run_case("cost gates on (market_costs=MarketCostConfig())", ticks,
                     ["SOLUSDT"], MarketCostConfig(), stop_loss_pct=0.5)
    if gated["total_trades"] != 0:
        raise SystemExit(
            "control failed: FROSTY+spot cost-aware run traded — audit finding "
            "not reproduced"
        )
    if flat["total_trades"] == 0:
        raise SystemExit(
            "control failed: flat FROSTY+spot run made no trades — the flat "
            "path is no longer the legacy gate-free behavior"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=200,
                        help="number of simulator ticks per run (default: 200)")
    parser.add_argument("--seed", type=int, default=42,
                        help="GBM simulator seed (default: 42)")
    args = parser.parse_args()

    symbols = ["SPY", "SOLUSDT"]
    ticks = generate_ticks(symbols, args.ticks, seed=args.seed)
    print(f"Reproducing calibration backtest claims ({args.ticks} ticks, seed {args.seed}, "
          f"symbols {', '.join(symbols)})")
    run_case("gates OFF (market_costs=None)", ticks, symbols, None)
    run_case("gates ON (market_costs=MarketCostConfig())", ticks, symbols,
             MarketCostConfig())

    frosty_spot_control(args.ticks, args.seed)
    print("\nOK: gates-off baseline is reproducible via market_costs=None and "
          "FROSTY+spot never trades with the gates on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
