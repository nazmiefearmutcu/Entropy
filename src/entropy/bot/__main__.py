from __future__ import annotations

import argparse
import asyncio
import contextlib

from .config import BotConfig, LiveConfig
from .execution.live import LIVE_WARNING
from .runner import BotRunner


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="entropy.bot", description="Entropy automatic trading bot")
    ap.add_argument("--mode", choices=["paper", "live"], default="paper")
    ap.add_argument("--risk", default="balanced", help="conservative | balanced | aggressive")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--no-crypto", action="store_true", help="disable the live crypto feed")
    ap.add_argument("--dashboard", action="store_true", help="run the TUI dashboard")
    ap.add_argument("--i-understand-the-risk", action="store_true",
                    help="required to even attempt live trading (see warning)")
    return ap.parse_args(argv)


def build_config(ns: argparse.Namespace) -> BotConfig:
    live = LiveConfig(enabled=(ns.mode == "live"), acknowledged_risk=ns.i_understand_the_risk)
    return BotConfig(mode=ns.mode, risk_profile=ns.risk, starting_cash=ns.cash,
                     enable_crypto=not ns.no_crypto, live=live)


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    if ns.mode == "live":
        print(LIVE_WARNING)
    cfg = build_config(ns)
    if ns.dashboard:
        from .ui.app import BotDashboard
        BotDashboard(cfg).run()
        return
    bot = BotRunner(cfg)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(bot.run())


if __name__ == "__main__":
    main()
