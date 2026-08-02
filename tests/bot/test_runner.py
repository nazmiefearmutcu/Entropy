import asyncio
import contextlib
from pathlib import Path

import pytest

from entropy.bot.config import BotConfig, build_strategies
from entropy.bot.runner import BotRunner
from entropy.engine.timeframe import get_timeframe


def test_build_strategies_from_names():
    cfg = BotConfig(strategies=("momentum_scalper", "ema_cross"), ema_symbol="SPY")
    strats = build_strategies(cfg)
    assert [s.name for s in strats] == ["momentum_scalper", "ema_cross"]


def test_on_trade_opens_position_on_momentum(tmp_path: Path):
    cfg = BotConfig(strategies=("momentum_scalper",), enable_crypto=False,
                    enable_equities=False, risk_profile="extreme", timeframe="1m")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    # First tick at ts=0 seeds the engine window AND the momentum anchor (returns no events).
    # Spacing comes from the CONFIGURED timeframe: has_anchor() only opens once a tick is at
    # least momentum_horizon_s newer than the anchor (30s on 1m, not the 5s of the old bare
    # Engine() default), and momentum_cooldown_ns gates the events after that. Ramping +1
    # from 100 gives ~1% moves vs the anchor -> classified as Spike (>=0.40%).
    spec = get_timeframe(cfg.timeframe)
    step = max(int(spec.momentum_horizon_s * 1_000_000_000), spec.momentum_cooldown_ns) + \
        1_000_000_000
    bot.on_trade("ZZZ", 100.0, 1.0, "buy", 0)
    for i in range(1, 6):
        bot.on_trade("ZZZ", 100.0 + i, 1.0, "buy", i * step)
    snap = bot.snapshot()
    assert snap.portfolio.open_count >= 1


def test_engine_follows_configured_timeframe(tmp_path: Path):
    """The bot's scanner cadence is a SETTING, not a hardcoded default.

    Regression guard: BotRunner used to build a bare ``Engine()``, pinning it to the
    legacy 30s/1m/5m windows no matter what the user asked for.
    """
    assert BotRunner(BotConfig(timeframe="1m"), run_dir=str(tmp_path / "a")
                     ).engine.cfg.window_labels == ("1m", "5m", "15m")
    assert BotRunner(BotConfig(timeframe="4h"), run_dir=str(tmp_path / "b")
                     ).engine.cfg.window_labels == ("4h", "12h", "1d")


def test_bar_seconds_follows_timeframe_unless_overridden():
    assert BotConfig(timeframe="1m").bar_seconds() == 60.0
    assert BotConfig(timeframe="15m").bar_seconds() == 900.0
    assert BotConfig(timeframe="15m", bar_s=30.0).bar_seconds() == 30.0


def test_pause_blocks_entries_but_never_exits(tmp_path: Path):
    """Pausing stops NEW risk; it must never strand an open position without its stop."""
    cfg = BotConfig(strategies=("momentum_scalper",), enable_crypto=False,
                    enable_equities=False, risk_profile="extreme", timeframe="1m")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    spec = get_timeframe(cfg.timeframe)
    step = max(int(spec.momentum_horizon_s * 1_000_000_000), spec.momentum_cooldown_ns) + \
        1_000_000_000
    bot.set_paused(True)
    bot.on_trade("ZZZ", 100.0, 1.0, "buy", 0)
    for i in range(1, 6):
        bot.on_trade("ZZZ", 100.0 + i, 1.0, "buy", i * step)
    assert bot.snapshot().portfolio.open_count == 0
    assert any("paused" in r for r in bot.snapshot().last_rejects)

    bot.set_paused(False)
    for i in range(6, 12):
        bot.on_trade("ZZZ", 100.0 + i, 1.0, "buy", i * step)
    assert bot.snapshot().portfolio.open_count >= 1


def test_apply_config_rejects_invalid_and_changes_nothing(tmp_path: Path):
    bot = BotRunner(BotConfig(timeframe="1m"), run_dir=str(tmp_path))
    before = bot.engine.cfg.window_labels
    problems = bot.apply_config(BotConfig(timeframe="1m", strategies=()))
    assert problems and "strategy" in problems[0]
    assert bot.engine.cfg.window_labels == before
    assert bot.config.strategies == ("consensus", "ema_cross")


def test_apply_config_hot_swaps_timeframe_and_risk(tmp_path: Path):
    bot = BotRunner(BotConfig(timeframe="1m", risk_profile="frosty"), run_dir=str(tmp_path))
    assert bot.apply_config(BotConfig(timeframe="1h", risk_profile="extreme")) == []
    assert bot.engine.cfg.window_labels == ("1h", "4h", "1d")
    assert bot.risk.profile.name == "Extreme"


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


def _smooth_uptrend(bot: BotRunner, symbol: str = "SPY", bars: int = 200,
                    bar_s: float = 5.0) -> None:
    """Feed a clean rising tape, one tick per strategy bar."""
    import random
    rng = random.Random(7)
    px = 100.0
    for i in range(bars):
        px *= 1.0 + 0.0015 + rng.uniform(-0.0004, 0.0004)
        bot.on_trade(symbol, px, 1.0, "buy", int(i * bar_s * 1_000_000_000) + 1)


def test_mechanical_exit_rearms_the_strategy(tmp_path: Path):
    """A take-profit must not mute the strategy for the rest of the trend.

    Strategies track what THEY signalled; the portfolio tracks what is open. A
    stop or take-profit closes the position without the strategy's knowledge, so
    it went on believing it was long and never opened again — one early
    take-profit silenced it for the whole move.
    """
    cfg = BotConfig(strategies=("consensus",), enable_crypto=False, enable_equities=False,
                    timeframe="1m", bar_s=5.0, risk_profile="frosty", warmup=False,
                    cost_aware=False)  # legacy mechanics test: no cost gates
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    _smooth_uptrend(bot)

    snap = bot.snapshot()
    entries = [s for s in snap.last_signals if "enter_long" in s]
    assert len(entries) > 1, "the strategy re-enters after each mechanical exit"
    consensus = bot.strategies[0]
    # strategy belief and portfolio reality agree at the end of the run
    believes_long = consensus._states["SPY"].direction == 1
    assert believes_long == (snap.portfolio.open_count == 1)


def test_rejected_entry_does_not_leave_a_phantom_position(tmp_path: Path):
    """A risk veto means the trade never happened — the strategy must not go on
    holding an imaginary one."""
    cfg = BotConfig(strategies=("consensus",), enable_crypto=False, enable_equities=False,
                    timeframe="1m", bar_s=5.0, warmup=False,
                    cost_aware=False)  # legacy mechanics test: no cost gates
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    bot.risk.trip()  # circuit breaker: every entry is now refused
    _smooth_uptrend(bot)

    consensus = bot.strategies[0]
    state = consensus._states.get("SPY")
    assert bot.snapshot().portfolio.open_count == 0
    assert state is not None and state.direction == 0
    assert any("circuit breaker" in r for r in bot.snapshot().last_rejects)


def test_strategy_requested_exit_is_not_double_reported(tmp_path: Path):
    """A reversal emits EXIT and ENTER_SHORT from one tick and has already
    recorded the new side internally; re-arming on that close would wipe it."""
    cfg = BotConfig(strategies=("ema_cross",), ema_symbol="SPY", enable_crypto=False,
                    enable_equities=False, risk_profile="extreme", warmup=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    ema = bot.strategies[0]
    px, ts = 100.0, 0
    for i in range(120):                     # up, then down: forces a cross both ways
        px *= 1.0 + (0.004 if i < 60 else -0.004)
        ts += 1_000_000_000
        bot.on_trade("SPY", px, 1.0, "buy", ts)
    from entropy.strategy.engine import Side
    pos = bot.portfolio.positions.get("SPY")
    if pos is not None:                      # core belief matches the open position
        assert ema._core.position.side is not Side.FLAT
