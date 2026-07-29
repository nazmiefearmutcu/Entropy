"""Config -> strategy -> runner, and the two-market claim end to end."""

from __future__ import annotations

import msgspec
import pytest

from entropy.bot.config import (
    STRATEGY_NAMES,
    BlackScholesConfig,
    BotConfig,
    build_strategies,
    validate,
)
from entropy.bot.risk.profiles import get_profile
from entropy.bot.runner import BotRunner
from entropy.bot.strategies.black_scholes import BlackScholesStrategy
from entropy.quant.conventions import Market
from entropy.quant.vol import ChainVolSource, RealizedVolSource

CRYPTO = "binance-spot:BTCUSDT"
EQUITY = "SPY"


def bs_runner(tmp_path, **bs) -> BotRunner:
    cfg = BotConfig(strategies=("black_scholes",), timeframe="1m", warmup=False,
                    black_scholes=BlackScholesConfig(**bs))
    return BotRunner(cfg, run_dir=str(tmp_path / "run"))


def with_bs(cfg: BotConfig, **bs) -> BotConfig:
    return msgspec.structs.replace(
        cfg, black_scholes=msgspec.structs.replace(cfg.black_scholes, **bs)
    )


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
    """Every value asserted here must differ from the constructor's own default.

    `BlackScholesConfig`'s defaults deliberately mirror
    `BlackScholesStrategy.__init__`'s, so any kwarg this test does not override
    with a DIFFERENT value is invisible to mutation: delete its wire in
    `build_strategies` and nothing goes red.
    """
    cfg = BotConfig(
        strategies=("black_scholes",),
        # Not 1m: a 1m bar is 60.0s, which is exactly the constructor's default
        # `bar_s`, so a never-threaded bar length would look correctly wired.
        timeframe="5m",
        black_scholes=BlackScholesConfig(
            horizon_bars=12, threshold=0.42, barrier_k=1.5,
            risk_free_rate=0.03, dividend_yield=0.01, crypto_carry_apr=0.09,
            exit_mode="either", vol_lambda=0.88, vol_floor=0.11,
        ),
    )
    strat = build_strategies(cfg)[0]
    assert isinstance(strat, BlackScholesStrategy)
    assert strat.horizon_bars == 12
    assert strat.threshold == pytest.approx(0.42)
    assert strat.barrier_k == pytest.approx(1.5)
    # A literal, not cfg.bar_seconds(): asserting against the same call
    # build_strategies makes would be a tautology on the derivation even now
    # that the value differs from the constructor default.
    assert strat.bar_s == pytest.approx(300.0)
    assert strat.exit_mode == "either"
    # One of the two intentional risk locks; RiskManager's independent min() is
    # the other. The constructor default is 100.0 and the medium profile allows
    # 2.5, so a dropped max_size_pct kwarg is a 40x sizing error that only the
    # surviving lock would contain. Belt-and-braces is worthless if either half
    # can vanish unnoticed.
    assert strat.max_size_pct == pytest.approx(2.5)
    # vol_lambda/vol_floor reach the strategy only through _build_vol_source.
    assert isinstance(strat.vol_source, RealizedVolSource)
    assert strat.vol_source.lam == pytest.approx(0.88)
    assert strat.vol_source.floor == pytest.approx(0.11)
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


def test_snapshot_exposes_the_strategy(tmp_path):
    cfg = BotConfig(strategies=("black_scholes",), timeframe="1m", warmup=False)
    runner = BotRunner(cfg, run_dir=str(tmp_path / "run"))
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


def test_funding_carry_source_is_refused_not_silently_ignored():
    """Nothing reads `crypto_carry_source`, so accepting "funding" would let a
    run claim funding carry and trade on a constant. Same contract
    `_build_vol_source` enforces for "chain": refuse loudly, never downgrade."""
    cfg = BotConfig(
        strategies=("black_scholes",),
        black_scholes=BlackScholesConfig(crypto_carry_source="funding"),
    )
    problems = validate(cfg)
    assert any("carry source" in p for p in problems), problems
    assert any("not supported yet" in p for p in problems), problems


def test_constant_carry_source_validates_clean():
    cfg = BotConfig(
        strategies=("black_scholes",),
        black_scholes=BlackScholesConfig(crypto_carry_source="constant"),
    )
    assert validate(cfg) == []


def test_threshold_above_the_reachable_score_is_refused():
    """0.7 used to validate clean and produce a strategy that can never enter.

    `divergence_score`'s supremum is ~0.5, not the 1.0 its defensive clamp
    suggests, so the old (0, 1] bound blessed a config the code cannot satisfy —
    and the strategy then said nothing about it, forever. The refusal is the
    diagnostic.
    """
    cfg = BotConfig(strategies=("black_scholes",),
                    black_scholes=BlackScholesConfig(threshold=0.7))
    problems = validate(cfg)
    assert any("threshold" in p and "supremum" in p for p in problems), problems
    # The boundary itself stays usable; only above it is refused.
    assert validate(with_bs(cfg, threshold=0.5)) == []


# ---- apply_config actually re-derives the running strategy -----------------


def test_apply_config_rebuilds_on_a_black_scholes_change(tmp_path):
    """A hot-applied BS setting must reach the STRATEGY, not just `self.config`.

    `strat_changed` compared `cfg.consensus` and never `cfg.black_scholes`, so
    every knob here changed what the config claimed and nothing that traded.
    `apply_bot` then persisted that config to ~/.entropy/settings.json, leaving a
    stored file describing a run that never happened.
    """
    runner = bs_runner(tmp_path)
    before = runner.strategies[0]
    assert (before.threshold, before.horizon_bars, before.exit_mode) == (
        0.15, 30, "score")

    assert runner.apply_config(
        with_bs(runner.config, threshold=0.4, horizon_bars=999, exit_mode="flip")
    ) == []

    after = runner.strategies[0]
    assert after is not before, "the strategy must be rebuilt, not left in place"
    assert (after.threshold, after.horizon_bars, after.exit_mode) == (
        0.4, 999, "flip")


def test_apply_config_swaps_the_live_vol_source(tmp_path):
    """The one that is a safety claim rather than a tuning knob.

    A stored `vol_source="chain"` against a bot still holding a
    `RealizedVolSource` is exactly the ledger lie `_build_vol_source` exists to
    prevent: "a run whose ledger says 'chain' must not have traded on something
    else."
    """
    runner = bs_runner(tmp_path)
    assert isinstance(runner.strategies[0].vol_source, RealizedVolSource)
    assert runner.apply_config(with_bs(runner.config, vol_source="chain")) == []
    assert isinstance(runner.strategies[0].vol_source, ChainVolSource)


def test_apply_config_refreshes_max_size_pct_on_a_profile_change(tmp_path):
    """`max_size_pct` is frozen at build time from `profile().per_trade_pct`.

    Without a rebuild a switch to Extreme left the strategy asking against
    Medium's 2.5%. That direction fails SAFE — and RiskManager's own min() is the
    second lock — but the strategy would be sized by a profile the run no longer
    runs, which is the same class of untruth as the vol source above.
    """
    runner = bs_runner(tmp_path)
    assert runner.strategies[0].max_size_pct == pytest.approx(2.5)
    assert runner.apply_config(
        msgspec.structs.replace(runner.config, risk_profile="extreme")) == []
    assert runner.strategies[0].max_size_pct == pytest.approx(
        get_profile("extreme").per_trade_pct)


def test_apply_config_leaves_the_strategy_alone_when_nothing_changed(tmp_path):
    """The guard on the fix: rebuilding is COLD, so it must not fire on a no-op.

    A rebuild throws away every warm indicator series, so widening
    `strat_changed` is only safe if an unrelated edit still leaves the strategy
    in place.
    """
    runner = bs_runner(tmp_path)
    before = runner.strategies[0]
    assert runner.apply_config(
        msgspec.structs.replace(runner.config, slippage_bps=7.0)) == []
    assert runner.strategies[0] is before


# ---- a refusal the user can actually see -----------------------------------


def test_a_refusing_vol_source_reaches_the_snapshot(tmp_path):
    """`vol_source="chain"` with no catalog refuses on every bar. Say so.

    `BlackScholesStrategy.last_rejects` was read by exactly one test and by no
    UI. `BotSnapshot.last_rejects` — the buffer the spec pointed at — is a
    different deque, appended only when a Signal EXISTED and was then rejected.
    A vol-source refusal produces no signal, so the bot looked warm, emitted
    nothing forever, and offered zero diagnostics anywhere a user can look.

    Driven through `BotRunner.on_trade` rather than the strategy directly: the
    gap was in the runner, so calling the strategy would have proved nothing.
    """
    runner = bs_runner(tmp_path, vol_source="chain")
    bar_ns = int(runner.config.bar_seconds() * 1_000_000_000)
    for i in range(120):
        runner.on_trade(CRYPTO, 30_000.0 * (1.0 + 0.001 * i), 1.0, "buy", i * bar_ns + 1)

    snap = runner.snapshot()
    assert snap.last_signals == (), "a refusing source must not signal"
    assert any(CRYPTO in r and "catalog" in r for r in snap.last_rejects), (
        snap.last_rejects)
    # Only the TRANSITION is recorded: 120 refusing bars must not flush the
    # 40-entry ring and bury every other reject the operator needs to read.
    assert len(snap.last_rejects) == 1


def test_a_recovered_symbol_can_refuse_again(tmp_path):
    """The de-duplication must not swallow a SECOND refusal after a recovery.

    Remembering "already reported" forever would turn an intermittent source
    into a single ancient line in the ring.
    """
    class Flaky:
        name = "flaky"
        last_reason = "chain went away"

        def __init__(self):
            self.ok = False

        def sigma(self, symbol, closes, conv):
            return 0.5 if self.ok else None

    runner = bs_runner(tmp_path)
    strat = runner.strategies[0]
    source = Flaky()
    strat.vol_source = source
    bar_ns = int(runner.config.bar_seconds() * 1_000_000_000)

    def run(lo, hi):
        for i in range(lo, hi):
            runner.on_trade(CRYPTO, 30_000.0 * (1.0 + 0.001 * i), 1.0, "buy",
                            i * bar_ns + 1)

    run(0, 60)
    assert len(runner.snapshot().last_rejects) == 1
    source.ok = True                      # recovers: the reject clears
    run(60, 90)
    source.ok = False                     # and refuses again
    run(90, 120)
    assert len([r for r in runner.snapshot().last_rejects
                if "chain went away" in r]) == 2


def test_strategy_views_carry_sigma_and_score(tmp_path):
    """`last_sigma`/`last_score` exist "for the dashboards" — so reach one.

    Both were written by the strategy and read by nothing outside its own test
    file. Below the entry threshold no signal is emitted and the reason string
    never reaches the UI either, so this is the only path on which a warm,
    silent, sub-threshold strategy is distinguishable from a stalled one.
    """
    runner = bs_runner(tmp_path)
    bar_ns = int(runner.config.bar_seconds() * 1_000_000_000)
    for i in range(120):
        runner.on_trade(CRYPTO, 30_000.0 * (1.0 + 0.0015 * i), 1.0, "buy",
                        i * bar_ns + 1)
    view = runner.snapshot().strategies[0]
    assert view.sigmas[CRYPTO] == pytest.approx(
        runner.strategies[0].last_sigma[CRYPTO])
    assert view.scores[CRYPTO] == pytest.approx(
        runner.strategies[0].last_score[CRYPTO])
    assert view.sigmas[CRYPTO] > 0.0
