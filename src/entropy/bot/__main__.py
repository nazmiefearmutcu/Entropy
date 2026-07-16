from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import time

from .config import BotConfig, LiveConfig
from .execution.live import LIVE_WARNING
from .runner import BotRunner


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
    return ap.parse_args(argv)


def build_config(ns: argparse.Namespace) -> BotConfig:
    live = LiveConfig(enabled=(ns.mode == "live"), acknowledged_risk=ns.i_understand_the_risk)
    kwargs = {}
    if getattr(ns, "console_log", None) is not None:
        kwargs["console_log_path"] = ns.console_log
    if getattr(ns, "trade_csv", None) is not None:
        kwargs["trade_csv_path"] = ns.trade_csv
    return BotConfig(mode=ns.mode, risk_profile=ns.risk, starting_cash=ns.cash,
                     enable_crypto=not ns.no_crypto, live=live, **kwargs)


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    if ns.mode == "live":
        print(LIVE_WARNING, flush=True)  # safety warning must surface even when stdout is piped
    cfg = build_config(ns)
    if cfg.console_log_path:
        log_dir = os.path.dirname(cfg.console_log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(cfg.console_log_path, "a") as f:
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
