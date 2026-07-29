"""Config -> strategy -> runner, and the two-market claim end to end."""

from __future__ import annotations

import pytest

from entropy.bot.config import (
    STRATEGY_NAMES,
    BlackScholesConfig,
    BotConfig,
    build_strategies,
    validate,
)
from entropy.bot.runner import BotRunner
from entropy.bot.strategies.black_scholes import BlackScholesStrategy
from entropy.quant.conventions import Market

CRYPTO = "binance-spot:BTCUSDT"
EQUITY = "SPY"


def test_black_scholes_is_selectable():
    assert "black_scholes" in STRATEGY_NAMES


def test_default_strategies_are_unchanged():
    """Opt-in: adding the strategy must not change what a default bot runs."""
    assert BotConfig().strategies == ("consensus", "ema_cross")


def test_build_strategies_constructs_it():
    cfg = BotConfig(strategies=("black_scholes",))
    built = build_strategies(cfg)
    assert len(built) == 1
    assert isinstance(built[0], BlackScholesStrategy)
    assert built[0].name == "black_scholes"


def test_config_values_reach_the_strategy():
    cfg = BotConfig(
        strategies=("black_scholes",),
        timeframe="1m",
        black_scholes=BlackScholesConfig(
            horizon_bars=12, threshold=0.42, barrier_k=1.5,
            risk_free_rate=0.03, dividend_yield=0.01, crypto_carry_apr=0.09,
        ),
    )
    strat = build_strategies(cfg)[0]
    assert isinstance(strat, BlackScholesStrategy)
    assert strat.horizon_bars == 12
    assert strat.threshold == pytest.approx(0.42)
    assert strat.barrier_k == pytest.approx(1.5)
    assert strat.bar_s == pytest.approx(cfg.bar_seconds())
    assert strat.convention_for(EQUITY).carry == pytest.approx(0.02)
    assert strat.convention_for(CRYPTO).carry == pytest.approx(0.09)


def test_the_same_strategy_serves_both_markets():
    """One instance, two symbols, two conventions — the two-market claim."""
    cfg = BotConfig(strategies=("black_scholes",), timeframe="1m")
    strat = build_strategies(cfg)[0]
    assert isinstance(strat, BlackScholesStrategy)
    assert strat.convention_for(CRYPTO).market is Market.CRYPTO
    assert strat.convention_for(EQUITY).market is Market.EQUITY
    assert strat.convention_for(CRYPTO).model == "black76"
    assert strat.convention_for(EQUITY).model == "bsm"
    assert (
        strat.convention_for(CRYPTO).bars_per_year
        != strat.convention_for(EQUITY).bars_per_year
    )


def test_runner_accepts_the_strategy_and_ticks_both_markets(tmp_path):
    cfg = BotConfig(strategies=("black_scholes",), timeframe="1m", warmup=False)
    runner = BotRunner(cfg, run_dir=str(tmp_path / "run"))
    strat = runner.strategies[0]
    assert isinstance(strat, BlackScholesStrategy)
    bar_ns = int(cfg.bar_seconds() * 1_000_000_000)
    for i in range(80):
        runner.on_trade(CRYPTO, 30_000.0 * (1.0 + 0.001 * i), 1.0, "buy", i * bar_ns)
        runner.on_trade(EQUITY, 100.0 * (1.0 + 0.001 * i), 1.0, "buy", i * bar_ns)
    assert strat.convention_for(CRYPTO).market is Market.CRYPTO
    assert strat.convention_for(EQUITY).market is Market.EQUITY
    assert runner.ticks == 160


def test_snapshot_exposes_the_strategy():
    cfg = BotConfig(strategies=("black_scholes",), timeframe="1m", warmup=False)
    runner = BotRunner(cfg, run_dir="runs/test-bs")
    names = [v.name for v in runner.snapshot().strategies]
    assert names == ["black_scholes"]


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        (dict(horizon_bars=0), "horizon"),
        (dict(threshold=0.0), "threshold"),
        (dict(threshold=1.5), "threshold"),
        (dict(vol_lambda=1.0), "lambda"),
        (dict(vol_floor=0.0), "floor"),
        (dict(barrier_k=0.0), "barrier"),
        (dict(drift_window=1), "drift window"),
        (dict(min_bars=5, drift_window=20), "drift window"),
        (dict(drift_shrinkage=1.5), "shrinkage"),
        (dict(drift_cap_sigmas=0.0), "cap"),
        (dict(z_stop=0.0), "z_stop"),
        (dict(z_tp=0.0), "z_tp"),
        (dict(stop_floor_pct=0.0), "stop floor"),
        (dict(risk_budget_pct=0.0), "risk budget"),
        (dict(vol_source="wat"), "vol source"),
        (dict(crypto_carry_source="wat"), "carry source"),
        (dict(exit_mode="wat"), "exit mode"),
    ],
)
def test_validate_reports_bad_black_scholes_settings(kwargs, fragment):
    cfg = BotConfig(strategies=("black_scholes",),
                    black_scholes=BlackScholesConfig(**kwargs))
    problems = validate(cfg)
    assert any(fragment in p for p in problems), problems


def test_a_default_config_validates_clean():
    assert validate(BotConfig(strategies=("black_scholes",))) == []
