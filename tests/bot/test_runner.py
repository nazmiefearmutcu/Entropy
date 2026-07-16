import asyncio
import contextlib
from pathlib import Path

import pytest

from entropy.bot.config import BotConfig, build_strategies
from entropy.bot.runner import BotRunner


def test_build_strategies_from_names():
    cfg = BotConfig(strategies=("momentum_scalper", "ema_cross"), ema_symbol="SPY")
    strats = build_strategies(cfg)
    assert [s.name for s in strats] == ["momentum_scalper", "ema_cross"]


def test_on_trade_opens_position_on_momentum(tmp_path: Path):
    cfg = BotConfig(strategies=("momentum_scalper",), enable_crypto=False,
                    enable_equities=False, risk_profile="extreme")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    # First tick at ts=0 seeds the engine window AND the momentum anchor (returns no events).
    # The momentum horizon is 5s (5e9 ns): has_anchor() only becomes true once a tick is at
    # least 5s newer than the anchor, so subsequent trades MUST be placed past 5e9 ns. Ramping
    # +1 from 100 over 100s gives ~1% moves vs the anchor → classified as Spike (>=0.40%).
    bot.on_trade("ZZZ", 100.0, 1.0, "buy", 0)
    for i in range(1, 6):
        bot.on_trade("ZZZ", 100.0 + i, 1.0, "buy", 5_000_000_000 + i * 1_000_000_000)
    snap = bot.snapshot()
    assert snap.portfolio.open_count >= 1


@pytest.mark.asyncio
async def test_run_with_sim_feed_records_equity(tmp_path: Path):
    cfg = BotConfig(strategies=("momentum_scalper",), enable_crypto=False,
                    enable_equities=True, equity_tps=3000, seed=11,
                    risk_profile="extreme")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    task = asyncio.create_task(bot.run())
    await asyncio.sleep(0.3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    # equity curve was written and the bot processed ticks deterministically
    assert (tmp_path / "equity.csv").exists()
    assert bot.ticks > 0


def test_set_risk_profile_records_change(tmp_path: Path):
    cfg = BotConfig(risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    assert bot.risk.profile.name == "Frosty"
    bot.set_risk_profile("extreme")
    assert bot.risk.profile.name == "Extreme"
    import json
    kinds = [json.loads(x)["kind"]
             for x in (tmp_path / "events.jsonl").read_text().strip().splitlines()]
    assert "risk_profile_changed" in kinds
