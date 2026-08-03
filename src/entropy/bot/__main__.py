from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import time

import msgspec

from entropy.engine.timeframe import TIMEFRAMES

from .config import STRATEGY_NAMES, BotConfig, LiveConfig, validate, warnings
from .execution.live import LIVE_WARNING
from .runner import BotRunner
from .strategies.consensus import VOTE_MODES

#: ``--<dest>`` CLI flags that override a single ``MarketCostConfig`` field.
#: The dest is the argparse name (dashes -> underscores), the value is the
#: struct field it feeds.
MARKET_COST_FLAGS: tuple[tuple[str, str], ...] = (
    ("spot_fee_bps", "crypto_spot_fee_bps"),
    ("spot_slippage_bps", "crypto_spot_slippage_bps"),
    ("futures_fee_bps", "crypto_futures_fee_bps"),
    ("futures_slippage_bps", "crypto_futures_slippage_bps"),
    ("equity_fee_bps", "equity_fee_bps"),
    ("equity_slippage_bps", "equity_slippage_bps"),
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="entropy.bot", description="Entropy automatic trading bot")
    ap.add_argument("--mode", choices=["paper", "live"], default="paper")
    ap.add_argument(
        "--risk", default="medium", type=str.lower,
        choices=["frosty", "medium", "extreme"],
        help="frosty | medium | extreme"
    )
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--no-crypto", action="store_true", help="disable the live crypto feed")
    ap.add_argument("--dashboard", action="store_true", help="run the TUI dashboard")
    ap.add_argument("--i-understand-the-risk", action="store_true",
                    help="required to even attempt live trading (see warning)")
    ap.add_argument("--console-log", default=None, help="console log path")
    ap.add_argument("--trade-csv", default=None, help="trade CSV path")
    # Cadence + signal logic: these used to be unreachable from outside the
    # source. `--timeframe` drives the bot's scanner windows AND (unless
    # --bar-s overrides it) the bar the strategies reason on.
    ap.add_argument("--timeframe", default=None, choices=sorted(TIMEFRAMES),
                    help="bot computation cadence (default: persisted setting, else 1m)")
    ap.add_argument("--bar-s", type=float, default=None,
                    help="strategy bar length in seconds; 0 = follow --timeframe")
    ap.add_argument("--strategies", default=None,
                    help=f"comma-separated: {','.join(STRATEGY_NAMES)}")
    ap.add_argument("--vote-mode", default=None, choices=list(VOTE_MODES),
                    help="consensus vote mapping; 'legacy' is the old trend-blind one")
    ap.add_argument("--no-warmup", action="store_true",
                    help="skip the startup history fetch")
    ap.add_argument("--cost-aware", dest="cost_aware",
                    action=argparse.BooleanOptionalAction, default=None,
                    help="enable/disable the cost-aware gate layer (default: persisted setting)")
    ap.add_argument("--cost-edge-mult", type=float, default=None,
                    help="cost gate multiplier k (default: persisted setting)")
    ap.add_argument("--max-cost-to-stop", type=float, default=None,
                    help="max round-trip cost as a fraction of stop distance "
                         "(default: persisted setting)")
    for dest, field in MARKET_COST_FLAGS:
        label = field.replace("_", " ").removesuffix(" bps")
        ap.add_argument(f"--{dest.replace('_', '-')}", type=float, default=None,
                        help=f"{label} override in bps (default: persisted setting)")
    ap.add_argument("--ignore-saved", action="store_true",
                    help="start from built-in defaults instead of ~/.entropy/settings.json")
    return ap.parse_args(argv)


def build_config(ns: argparse.Namespace) -> BotConfig:
    """CLI flags layered over the PERSISTED bot settings.

    Only flags the user actually typed override the saved config: argparse
    defaults would otherwise silently reset whatever was configured in the
    GUI/TUI every time the bot was launched.
    """
    from entropy import settings

    base = BotConfig() if ns.ignore_saved else settings.load().bot
    kwargs: dict[str, object] = {
        "mode": ns.mode,
        "risk_profile": ns.risk,
        "starting_cash": ns.cash,
        "enable_crypto": not ns.no_crypto,
        "live": LiveConfig(enabled=(ns.mode == "live"),
                           acknowledged_risk=ns.i_understand_the_risk),
    }
    if ns.console_log is not None:
        kwargs["console_log_path"] = ns.console_log
    if ns.trade_csv is not None:
        kwargs["trade_csv_path"] = ns.trade_csv
    if ns.timeframe is not None:
        kwargs["timeframe"] = ns.timeframe
    if ns.bar_s is not None:
        kwargs["bar_s"] = ns.bar_s
    if ns.strategies is not None:
        kwargs["strategies"] = tuple(s.strip() for s in ns.strategies.split(",") if s.strip())
    if ns.vote_mode is not None:
        kwargs["consensus"] = msgspec.structs.replace(base.consensus, vote_mode=ns.vote_mode)
    if ns.no_warmup:
        kwargs["warmup"] = False
    if ns.cost_aware is not None:
        kwargs["cost_aware"] = ns.cost_aware
    if ns.cost_edge_mult is not None:
        kwargs["cost_edge_mult"] = ns.cost_edge_mult
    if ns.max_cost_to_stop is not None:
        kwargs["max_cost_to_stop"] = ns.max_cost_to_stop
    market_cost_changes: dict[str, object] = {}
    for dest, field in MARKET_COST_FLAGS:
        value = getattr(ns, dest)
        if value is not None:
            market_cost_changes[field] = value
    if market_cost_changes:
        kwargs["market_costs"] = msgspec.structs.replace(
            base.market_costs, **market_cost_changes
        )
    return msgspec.structs.replace(base, **kwargs)


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    if ns.mode == "live":
        print(LIVE_WARNING, flush=True)  # safety warning must surface even when stdout is piped
    cfg = build_config(ns)
    problems = validate(cfg)
    if problems:
        # Fail loudly at the door rather than surfacing later as a stack trace
        # from inside a feed, which is what an unvalidated config used to do.
        for problem in problems:
            print(f"config error: {problem}", file=sys.stderr)
        raise SystemExit(2)
    for warning in warnings(cfg):
        # Partial cost-gate overruns (some markets dead, others trading) are
        # non-fatal: surface them, then start.
        print(f"config warning: {warning}", file=sys.stderr)
    print(f"cadence: {cfg.timeframe} scanner / {cfg.bar_seconds():g}s strategy bars · "
          f"strategies: {', '.join(cfg.strategies)} · "
          f"consensus vote mode: {cfg.consensus.vote_mode}", flush=True)
    if cfg.console_log_path:
        log_dir = os.path.dirname(cfg.console_log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(cfg.console_log_path, "a"):
            pass
    # A fresh timestamped run dir per launch so paper and live ledgers never mix.
    run_dir = f"runs/{ns.mode}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    bot = BotRunner(cfg, run_dir=run_dir)
    if ns.dashboard:
        from .ui.app import BotDashboard
        BotDashboard(cfg, runner=bot).run()
        return
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(bot.run())


if __name__ == "__main__":
    main()
