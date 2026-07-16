import asyncio
import contextlib
import csv
from pathlib import Path

import pytest

from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner


def _replay_fixed_sequence(run_dir: Path) -> BotRunner:
    """Drive a FIXED trade sequence through a fresh runner synchronously (no live/async
    feed). All timestamps sit past the 5s momentum horizon so momentum events fire."""
    cfg = BotConfig(strategies=("momentum_scalper", "ema_cross"), ema_symbol="AAA",
                    enable_crypto=False, enable_equities=False, risk_profile="extreme")
    bot = BotRunner(cfg, run_dir=str(run_dir))
    bot.on_trade("AAA", 100.0, 1.0, "buy", 0)   # seed window + momentum anchor
    bot.on_trade("BBB", 50.0, 1.0, "buy", 0)
    prices_a = [101, 103, 102, 105, 104, 107, 99, 95, 101, 110, 108, 112, 107, 115]
    prices_b = [50.5, 51, 49, 52, 48, 53, 47, 54, 46, 55, 45, 56, 44, 57]
    base = 5_000_000_000
    for i, (pa, pb) in enumerate(zip(prices_a, prices_b, strict=True), start=1):
        ts = base + i * 1_000_000_000
        bot.on_trade("AAA", float(pa), 1.0, "buy", ts)
        bot.on_trade("BBB", float(pb), 1.0, "sell", ts)
    return bot


def test_on_trade_path_is_deterministic(tmp_path: Path):
    """The synchronous on_trade decision+paper-fill path is deterministic: replaying an
    identical trade sequence through two fresh runners yields identical ticks, equity,
    realized PnL, and an identical fill ledger."""
    b1 = _replay_fixed_sequence(tmp_path / "x")
    b2 = _replay_fixed_sequence(tmp_path / "y")
    assert b1.ticks == b2.ticks > 0
    assert b1.portfolio.equity() == b2.portfolio.equity()
    assert b1.portfolio.realized_pnl == b2.portfolio.realized_pnl
    assert (tmp_path / "x" / "fills.csv").read_text() == (tmp_path / "y" / "fills.csv").read_text()
    # the path actually traded (sanity: at least one fill row beyond the header)
    assert len((tmp_path / "x" / "fills.csv").read_text().splitlines()) > 1


@pytest.mark.asyncio
async def test_end_to_end_paper_run_writes_valid_ledger(tmp_path: Path):
    """End-to-end async smoke: the bot runs over the live sim feed and writes a valid,
    parseable equity-curve ledger. (Exact tick counts vary because the feed is driven by
    wall-clock asyncio.sleep; strict path determinism is covered by the replay test above.)"""
    async def run_once(d: Path) -> int:
        cfg = BotConfig(strategies=("momentum_scalper", "ema_cross"), ema_symbol="SPY",
                        enable_crypto=False, enable_equities=True, equity_tps=3000,
                        seed=7, risk_profile="extreme")
        bot = BotRunner(cfg, run_dir=str(d), equity_record_period_s=0.05)
        task = asyncio.create_task(bot.run())
        await asyncio.sleep(0.3)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return bot.ticks

    t1 = await run_once(tmp_path / "a")
    assert t1 > 0
    rows = list(csv.DictReader((tmp_path / "a" / "equity.csv").open()))
    assert len(rows) > 0
    assert all("equity" in r for r in rows)
