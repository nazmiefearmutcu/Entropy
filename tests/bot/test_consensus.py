"""ConsensusStrategy: deterministic synthetic-path tests (no network).

Paths are ordinary market shapes — a clean trend, a reversal, dead chop — and
the strategy is expected to handle them as written. That is a deliberate change
from the previous version of this file, which had to contort its paths ("a
zigzag rise keeps RSI inside the 30-70 band so the trend votes can reach
consensus") to work around the strategy being blind to real trends. A test
should not have to apologise for the code it covers.

``test_legacy_mapping_is_trend_blind`` pins the original pathology so a
regression cannot reintroduce it quietly.
"""

from __future__ import annotations

import math
import random

import pytest

from entropy.bot.config import BotConfig, build_strategies, validate
from entropy.bot.costs import CostModel
from entropy.bot.signals import SignalAction
from entropy.bot.strategies.consensus import (
    DEFAULT_WEIGHTS,
    ConsensusStrategy,
    Votes,
    _band_vote,
    _cmp_vote,
    _momentum_vote,
    _percent_b,
    _sign_vote,
    score_votes,
    tilt_weights,
    trend_score,
)
from entropy.strategy.engine import Bar

_NS = 1_000_000_000
_BAR_NS = 5 * _NS  # matches the default bar_s=5.0


def feed_bars(strat: ConsensusStrategy, symbol: str, closes: list[float],
              start_bucket: int = 0) -> list[tuple[int, SignalAction]]:
    """One tick per 5s bucket; returns (bar_index, action) for emitted signals."""
    out: list[tuple[int, SignalAction]] = []
    for i, px in enumerate(closes):
        ts = (start_bucket + i) * _BAR_NS + 1
        for sig in strat.on_tick(symbol, px, ts, events=[]):
            out.append((start_bucket + i, sig.action))
    return out


def path_trend(seed: int, direction: int = 1, n: int = 120,
               drift: float = 0.0015, scale: float = 1.0) -> list[float]:
    """Quiet base, then a smooth directional move: high efficiency ratio."""
    rng = random.Random(seed)
    px, out = 100.0, []
    for _ in range(40):  # flat base so the EMAs seed without a stale gap
        px *= 1.0 + scale * rng.uniform(-0.0002, 0.0002)
        out.append(px)
    for _ in range(n):
        px *= 1.0 + scale * (direction * drift + rng.uniform(-0.0004, 0.0004))
        out.append(px)
    return out


def path_reversal(seed: int) -> list[float]:
    """Clean uptrend, then a clean downtrend."""
    up = path_trend(seed, direction=1, n=100)
    rng = random.Random(seed + 999)
    px, down = up[-1], []
    for _ in range(120):
        px *= 1.0 - 0.0015 + rng.uniform(-0.0004, 0.0004)
        down.append(px)
    return up + down


def path_chop(seed: int, n: int = 140) -> list[float]:
    """Dead flat: +-0.02% noise around 100, i.e. nothing worth trading."""
    rng = random.Random(seed)
    return [100.0 * (1.0 + rng.uniform(-0.0002, 0.0002)) for _ in range(n)]


# ---- the headline regression ------------------------------------------------


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_legacy_mapping_is_trend_blind(seed):
    """The bug this strategy was rewritten to fix, pinned.

    The original mapping had EMA/MACD vote trend-following while RSI/Bollinger
    voted mean-reversion. A sustained rally pins RSI above 70 and %B above 0.95,
    so the oscillators subtracted 0.35 from a 0.65 trend score and the total
    (0.30) never cleared the 0.50 threshold: a textbook trend produced NOTHING.
    """
    closes = path_trend(seed, direction=1)
    legacy = ConsensusStrategy(symbols=("SPY",), vote_mode="legacy")
    adaptive = ConsensusStrategy(symbols=("SPY",), vote_mode="adaptive")
    assert feed_bars(legacy, "SPY", closes) == []
    assert [a for _, a in feed_bars(adaptive, "SPY", closes)] == [SignalAction.ENTER_LONG]


# ---- trend / hysteresis / regime ------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11, 42])
def test_uptrend_exactly_one_enter_long_no_churn(seed):
    strat = ConsensusStrategy(symbols=("SPY",))
    events = feed_bars(strat, "SPY", path_trend(seed, direction=1))
    assert [a for _, a in events] == [SignalAction.ENTER_LONG]
    # hysteresis + min_hold held the position through the rest of the trend
    assert strat._states["SPY"].direction == 1


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11, 42])
def test_downtrend_exactly_one_enter_short(seed):
    strat = ConsensusStrategy(symbols=("SPY",))
    events = feed_bars(strat, "SPY", path_trend(seed, direction=-1))
    assert [a for _, a in events] == [SignalAction.ENTER_SHORT]
    assert strat._states["SPY"].direction == -1


@pytest.mark.parametrize("seed", [3, 7, 42])
def test_flat_chop_zero_entries(seed):
    strat = ConsensusStrategy(symbols=("SPY",))
    assert feed_bars(strat, "SPY", path_chop(seed)) == []


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_regime_filter_blocks_scaled_down_trend(seed):
    """Same shape scaled 10x down: the votes are scale-invariant so the score
    still reaches the threshold, but per-bar movement falls under move_floor
    -> the regime filter alone must block every entry."""
    strat = ConsensusStrategy(symbols=("SPY",))
    closes = path_trend(seed, direction=1, drift=0.00015, scale=0.1)
    assert feed_bars(strat, "SPY", closes) == []


def test_consensus_sigma_gate_blocks_marginal_volatility():
    """Red side of the amortized cost sigma gate: per-bar movement clears the
    (k*C)/W move floor but the RMS bar move stays under (k*C)/(E_ABS_MOVE*W),
    so the cost-aware strategy must NOT enter while the plain one does."""
    cm = CostModel(flat_fee_bps=10.0, flat_slippage_bps=3.0)  # C=26bps, k*C=52bps
    rng = random.Random(3)
    px, closes = 100.0, []
    for _ in range(40):
        px *= 1.0 + rng.uniform(-0.00005, 0.00005)
        closes.append(px)
    for i in range(120):  # alternating 4bp/2bp: mean|r|=3bp > 2.6bp floor,
        px *= 1.0 + (0.0004 if i % 2 == 0 else 0.0002)  # but RMS ~3.16bp < 3.26bp
        closes.append(px)
    plain = ConsensusStrategy(symbols=("SPY",), move_floor=0.0)
    gated = ConsensusStrategy(symbols=("SPY",), move_floor=0.0, costs=cm)
    assert SignalAction.ENTER_LONG in [a for _, a in feed_bars(plain, "SPY", closes)]
    assert feed_bars(gated, "SPY", closes) == []


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11, 42])
def test_reversal_exit_then_enter_short(seed):
    strat = ConsensusStrategy(symbols=("SPY",))
    events = feed_bars(strat, "SPY", path_reversal(seed))
    assert [a for _, a in events] == [
        SignalAction.ENTER_LONG, SignalAction.EXIT, SignalAction.ENTER_SHORT,
    ]
    long_bar, exit_bar, short_bar = (b for b, _ in events)
    assert long_bar < exit_bar < short_bar
    assert strat._states["SPY"].direction == -1


def test_min_hold_bars_blocks_an_immediate_exit():
    strat = ConsensusStrategy(symbols=("SPY",), min_hold_bars=25)
    events = feed_bars(strat, "SPY", path_reversal(7))
    assert events[0][1] is SignalAction.ENTER_LONG
    entry_bar = events[0][0]
    exits = [b for b, a in events if a is SignalAction.EXIT]
    assert exits and all(b - entry_bar >= 25 for b in exits)


def test_cooldown_bars_delays_re_entry():
    """After an exit the strategy must sit out `cooldown_bars` completed bars."""
    closes = path_reversal(7)
    quick = ConsensusStrategy(symbols=("SPY",), cooldown_bars=0)
    patient = ConsensusStrategy(symbols=("SPY",), cooldown_bars=40)
    q_short = next(b for b, a in feed_bars(quick, "SPY", closes)
                   if a is SignalAction.ENTER_SHORT)
    p_short = next(b for b, a in feed_bars(patient, "SPY", closes)
                   if a is SignalAction.ENTER_SHORT)
    assert p_short > q_short


def test_regime_labels_are_published_for_telemetry():
    strat = ConsensusStrategy(symbols=("SPY",))
    feed_bars(strat, "SPY", path_trend(7, direction=1))
    assert strat.last_regime["SPY"].label == "trend"
    assert strat.last_regime["SPY"].efficiency >= strat.trend_er

    calm = ConsensusStrategy(symbols=("SPY",))
    feed_bars(calm, "SPY", path_chop(7))
    assert calm.last_regime["SPY"].label == "chop"
    assert not calm.last_regime["SPY"].tradeable


def test_smooth_trend_has_low_dispersion_but_is_still_tradeable():
    """Why the regime test moved off realized volatility.

    A clean trend is the LOWEST-dispersion interesting market there is, so a
    stdev-of-returns floor labelled it "chop" and refused to trade it. Movement
    and directionality are separate questions and are now measured separately.
    """
    strat = ConsensusStrategy(symbols=("SPY",))
    feed_bars(strat, "SPY", path_trend(7, direction=1))
    regime = strat.last_regime["SPY"]
    assert regime.tradeable          # enough movement per bar
    assert regime.efficiency > 0.5   # and that movement goes somewhere


# ---- warmup ----------------------------------------------------------------


def _bars(closes: list[float]) -> list[Bar]:
    return [Bar(ts_ns=i * _BAR_NS, close=c) for i, c in enumerate(closes)]


def test_warmup_split_matches_all_live_feed():
    """Warming from history is indistinguishable from having watched it live.

    The split point must sit BEFORE the first signal, otherwise the warmed run
    legitimately misses a signal the live run saw and the two cannot agree.
    """
    closes = path_trend(7, direction=1)
    live = ConsensusStrategy(symbols=("SPY",))
    whole = feed_bars(live, "SPY", closes)
    assert whole  # the path really does signal, so the equality is not vacuous
    split_at = whole[0][0] - 5

    warmed = ConsensusStrategy(symbols=("SPY",))
    warmed.warmup(_bars(closes[:split_at]))
    assert feed_bars(warmed, "SPY", closes[split_at:], start_bucket=split_at) == whole


def test_warmup_makes_strategy_immediately_eligible():
    """Warmed right up to the entry bar: the signal must come within a few live
    bars, with no fresh min_bars (35-bar) wait."""
    closes = path_trend(7, direction=1)
    cold = ConsensusStrategy(symbols=("SPY",))
    entry_bar = feed_bars(cold, "SPY", closes)[0][0]

    strat = ConsensusStrategy(symbols=("SPY",))
    strat.warmup(_bars(closes[:entry_bar]))
    events = feed_bars(strat, "SPY", closes[entry_bar:entry_bar + 12],
                       start_bucket=entry_bar)
    assert events and events[0][1] is SignalAction.ENTER_LONG
    assert events[0][0] <= entry_bar + 5  # far below min_bars=35 live bars


def test_warmup_symbol_attributes_the_seed_deterministically():
    """With symbols=None the seed used to land on whichever symbol ticked first.

    A `warmup_symbol` makes the attribution explicit, so an unrelated instrument
    can no longer inherit another one's price history.
    """
    closes = path_trend(7, direction=1)
    strat = ConsensusStrategy(warmup_symbol="SPY")
    strat.warmup(_bars(closes[:60]))
    assert "SPY" in strat._states
    assert strat._seed is None
    # an unrelated symbol starts cold rather than adopting SPY's closes
    strat.on_tick("BTCUSDT", 50_000.0, 61 * _BAR_NS, events=[])
    assert list(strat._states["BTCUSDT"].closes) == []


def test_warmup_none_symbols_seeds_first_symbol_seen():
    closes = path_trend(7, direction=1)
    strat = ConsensusStrategy()  # symbols=None, no warmup_symbol -> best effort
    strat.warmup(_bars(closes[:60]))
    events = feed_bars(strat, "BTCUSDT", closes[60:], start_bucket=60)
    assert events and events[0][1] is SignalAction.ENTER_LONG


def test_warmup_empty_is_noop():
    strat = ConsensusStrategy(symbols=("SPY",))
    strat.warmup([])
    assert strat._states == {}


# ---- scoring (table-driven) -------------------------------------------------


@pytest.mark.parametrize(
    ("votes", "weights", "expected"),
    [
        (Votes(1, 1, 0, 0), DEFAULT_WEIGHTS, 0.65),
        (Votes(1, 1, 1, 1), DEFAULT_WEIGHTS, 1.0),
        (Votes(-1, -1, -1, -1), DEFAULT_WEIGHTS, -1.0),
        (Votes(1, 1, -1, -1), DEFAULT_WEIGHTS, 0.30),
        (Votes(-1, -1, 1, 0), DEFAULT_WEIGHTS, -0.45),
        (Votes(0, 0, 0, 0), DEFAULT_WEIGHTS, 0.0),
        # custom weights are normalized by their total into [-1, 1]
        (Votes(1, 1, 0, 0), {"ema": 0.5, "macd": 0.5, "rsi": 0.0, "bollinger": 0.0}, 1.0),
        (Votes(1, -1, 0, 0), {"ema": 3.0, "macd": 1.0}, 0.5),
        (Votes(1, 1, 1, 1), {}, 0.0),
    ],
)
def test_score_votes_total_normalization(votes, weights, expected):
    assert score_votes(votes, weights) == pytest.approx(expected)


def test_participating_normalization_ignores_abstentions():
    """Two indicators agreeing while the others abstain is full agreement AMONG
    THOSE WITH AN OPINION — 0.65 under total normalization, 1.0 under
    participating."""
    votes = Votes(1, 1, 0, 0)
    assert score_votes(votes, DEFAULT_WEIGHTS) == pytest.approx(0.65)
    assert score_votes(votes, DEFAULT_WEIGHTS, normalize="participating") == pytest.approx(1.0)


def test_min_participation_blocks_a_lone_voter():
    """A single indicator must not be read as unanimity."""
    lone = Votes(0, 0, 1, 0)  # rsi alone: 0.20 of 1.0 total weight
    assert score_votes(lone, DEFAULT_WEIGHTS, normalize="participating",
                       min_participation=0.0) == pytest.approx(1.0)
    assert score_votes(lone, DEFAULT_WEIGHTS, normalize="participating",
                       min_participation=0.5) == 0.0


def test_tilt_weights_promotes_the_informative_block():
    trending = tilt_weights(DEFAULT_WEIGHTS, trending=True, tilt=2.0)
    ranging = tilt_weights(DEFAULT_WEIGHTS, trending=False, tilt=2.0)
    assert trending["ema"] == pytest.approx(0.70)
    assert trending["rsi"] == pytest.approx(0.20)
    assert ranging["ema"] == pytest.approx(0.35)
    assert ranging["rsi"] == pytest.approx(0.40)


def test_tilt_blocks_a_trend_only_entry_while_ranging():
    """EMA+MACD agreeing with silent oscillators is unanimity in a TREND, but in
    a RANGE the tilt drops their share below the participation floor, so the
    same votes score zero instead of opening a position."""
    votes = Votes(1, 1, 0, 0)
    trending = score_votes(votes, tilt_weights(DEFAULT_WEIGHTS, trending=True, tilt=2.0),
                           normalize="participating", min_participation=0.5)
    ranging = score_votes(votes, tilt_weights(DEFAULT_WEIGHTS, trending=False, tilt=2.0),
                          normalize="participating", min_participation=0.5)
    assert trending == pytest.approx(1.0)
    assert ranging == 0.0


def test_trend_score_isolates_the_trend_block():
    assert trend_score(Votes(1, 1, -1, -1), DEFAULT_WEIGHTS) == pytest.approx(1.0)
    assert trend_score(Votes(-1, -1, 1, 1), DEFAULT_WEIGHTS) == pytest.approx(-1.0)
    assert trend_score(Votes(1, -1, 0, 0), {"ema": 3.0, "macd": 1.0}) == pytest.approx(0.5)
    assert trend_score(Votes(1, 1, 1, 1), {"rsi": 1.0}) == 0.0


def test_score_side_vs_threshold_semantics():
    """|score| >= threshold picks the side; the sign picks long vs short."""
    threshold = 0.5
    long_score = score_votes(Votes(1, 1, 0, 0), DEFAULT_WEIGHTS)
    short_score = score_votes(Votes(-1, -1, 0, 0), DEFAULT_WEIGHTS)
    blocked = score_votes(Votes(1, 1, -1, -1), DEFAULT_WEIGHTS)
    assert long_score >= threshold and short_score <= -threshold
    assert abs(blocked) < threshold


def test_vote_helpers():
    assert _cmp_vote(2.0, 1.0) == 1
    assert _cmp_vote(1.0, 2.0) == -1
    assert _cmp_vote(1.0, 1.0) == 0
    assert _cmp_vote(None, 1.0) == 0
    assert _sign_vote(0.5) == 1
    assert _sign_vote(-0.5) == -1
    assert _sign_vote(0.0) == 0
    assert _sign_vote(None) == 0
    assert _band_vote(20.0, low=30.0, high=70.0) == 1  # oversold -> long
    assert _band_vote(80.0, low=30.0, high=70.0) == -1  # overbought -> short
    assert _band_vote(50.0, low=30.0, high=70.0) == 0
    assert _band_vote(None, low=30.0, high=70.0) == 0
    assert _percent_b(105.0, upper=110.0, lower=100.0) == pytest.approx(0.5)
    assert _percent_b(100.0, upper=100.0, lower=100.0) is None  # degenerate band


def test_momentum_vote_is_the_mirror_of_band_vote():
    """Same reading, opposite conclusion — which is the whole point of the
    regime switch."""
    for value in (10.0, 20.0, 50.0, 80.0, 95.0):
        assert _momentum_vote(value, low=30.0, high=70.0) == -_band_vote(
            value, low=30.0, high=70.0
        )
    assert _momentum_vote(None, low=30.0, high=70.0) == 0


def test_rsi_monotonic_series_reads_overbought_but_votes_with_the_trend():
    """Cross-check against the real indicator: a strictly rising series has
    RSI = 100. Read the mean-reverting way that is -1 (short a rally); the
    trending regime reads it +1, which is the corrected behaviour."""
    from crocodile.core.analytics.indicators import calculate_rsi

    rsi = calculate_rsi([100.0 + i for i in range(30)], 14)
    assert rsi[-1] == pytest.approx(100.0)
    assert _band_vote(rsi[-1], low=30.0, high=70.0) == -1
    assert _momentum_vote(rsi[-1], low=45.0, high=55.0) == 1


# ---- bar aggregation ---------------------------------------------------------


def test_ticks_inside_bucket_never_evaluate_roll_evaluates_once(monkeypatch):
    strat = ConsensusStrategy(symbols=("SPY",), min_bars=5)
    calls: list[int] = []
    orig = strat._evaluate

    def spy(symbol, ts_ns, st):
        calls.append(ts_ns)
        return orig(symbol, ts_ns, st)

    monkeypatch.setattr(strat, "_evaluate", spy)
    for k in range(10):  # ten ticks inside bucket 0
        strat.on_tick("SPY", 100.0 + k, k * _NS // 4, events=[])
    assert calls == []
    strat.on_tick("SPY", 101.0, _BAR_NS + 1, events=[])  # roll into bucket 1
    assert len(calls) == 1
    strat.on_tick("SPY", 101.5, _BAR_NS + 2 * _NS, events=[])  # same bucket
    assert len(calls) == 1
    strat.on_tick("SPY", 102.0, 5 * _BAR_NS, events=[])  # gap skip -> one roll
    assert len(calls) == 2


def test_first_tick_ever_does_not_evaluate(monkeypatch):
    strat = ConsensusStrategy(symbols=("SPY",), min_bars=1)
    monkeypatch.setattr(strat, "_evaluate", lambda *a: pytest.fail("evaluated"))
    assert strat.on_tick("SPY", 100.0, 123 * _BAR_NS, events=[]) == []


def test_bar_close_tracks_last_tick_in_bucket():
    strat = ConsensusStrategy(symbols=("SPY",))
    strat.on_tick("SPY", 100.0, 1, events=[])
    strat.on_tick("SPY", 105.0, 2, events=[])  # same bucket: close updates
    strat.on_tick("SPY", 90.0, _BAR_NS + 1, events=[])  # roll commits 105.0
    assert list(strat._states["SPY"].closes) == [105.0]


def test_min_bars_is_raised_to_cover_the_configured_indicators():
    """A 5-bar minimum with a 60-period Bollinger would evaluate bars on which
    every indicator can only abstain."""
    strat = ConsensusStrategy(min_bars=5, bb_period=60)
    assert strat.min_bars >= 60


# ---- symbol filter -----------------------------------------------------------


def test_symbols_tuple_filters_other_symbols():
    strat = ConsensusStrategy(symbols=("BTCUSDT",))
    assert strat.on_tick("SPY", 100.0, 1, events=[]) == []
    assert "SPY" not in strat._states


def test_symbols_none_accepts_all():
    strat = ConsensusStrategy()
    strat.on_tick("SPY", 100.0, 1, events=[])
    strat.on_tick("BTCUSDT", 50_000.0, 1, events=[])
    assert set(strat._states) == {"SPY", "BTCUSDT"}


# ---- config wiring -----------------------------------------------------------


def test_default_strategies_include_consensus():
    cfg = BotConfig()
    assert cfg.strategies == ("consensus",)
    strats = build_strategies(cfg)
    assert [s.name for s in strats] == ["consensus"]
    assert isinstance(strats[0], ConsensusStrategy)


def test_momentum_scalper_still_buildable_by_name():
    cfg = BotConfig(strategies=("momentum_scalper", "consensus"))
    assert [s.name for s in build_strategies(cfg)] == ["momentum_scalper", "consensus"]


def test_consensus_config_reaches_the_strategy():
    """Every knob used to be hardcoded; this is the wire that makes the settings
    panels meaningful."""
    from entropy.bot.config import ConsensusConfig

    cfg = BotConfig(
        strategies=("consensus",), timeframe="5m", ema_symbol="binance-spot:BTCUSDT",
        consensus=ConsensusConfig(
            threshold=0.7, vote_mode="trend", normalize="total", rsi_period=21,
            bb_period=30, macd_fast=8, macd_slow=17, macd_signal=5,
            min_hold_bars=9, exit_mode="trend_flip", w_ema=0.5,
        ),
    )
    strat = build_strategies(cfg)[0]
    assert isinstance(strat, ConsensusStrategy)
    assert strat.bar_s == 300.0            # follows the 5m timeframe
    assert strat.threshold == 0.7
    assert strat.vote_mode == "trend"
    assert strat.normalize == "total"
    assert (strat.rsi_period, strat.bb_period) == (21, 30)
    assert (strat.macd_fast, strat.macd_slow, strat.macd_signal) == (8, 17, 5)
    assert strat.min_hold_bars == 9
    assert strat.exit_mode == "trend_flip"
    assert strat.weights["ema"] == 0.5
    assert strat.warmup_symbol == "binance-spot:BTCUSDT"


def test_bar_s_override_beats_the_timeframe():
    cfg = BotConfig(strategies=("consensus",), timeframe="15m", bar_s=45.0)
    assert build_strategies(cfg)[0].bar_s == 45.0


def test_legacy_mode_pins_its_own_scoring():
    """`legacy` must be the ORIGINAL strategy, not the old votes under the new
    scoring — so it forces total normalization and no participation floor."""
    strat = ConsensusStrategy(vote_mode="legacy", normalize="participating",
                              min_participation=0.9)
    assert strat.normalize == "total"
    assert strat.min_participation == 0.0


def test_ctor_validation():
    with pytest.raises(ValueError):
        ConsensusStrategy(bar_s=0.0)
    with pytest.raises(ValueError):
        ConsensusStrategy(threshold=0.0)
    with pytest.raises(ValueError):
        ConsensusStrategy(threshold=1.5)
    with pytest.raises(ValueError):
        ConsensusStrategy(vote_mode="vibes")
    with pytest.raises(ValueError):
        ConsensusStrategy(normalize="whatever")
    with pytest.raises(ValueError):
        ConsensusStrategy(exit_mode="eventually")
    with pytest.raises(ValueError):
        ConsensusStrategy(ema_fast=21, ema_slow=9)
    with pytest.raises(ValueError):
        ConsensusStrategy(macd_fast=26, macd_slow=12)
    with pytest.raises(ValueError):
        ConsensusStrategy(rsi_period=0)


# ---- the measured behavioural claim -----------------------------------------


def _naive_directional_pnl(events, closes) -> float:
    """Mark-to-market of the SIGNALLED direction, no fees. Measures whether the
    strategy was pointed the right way — NOT a backtest and NOT an edge claim."""
    total, pos, entry = 0.0, 0, 0.0
    for bar, action in events:
        px = closes[bar]
        if action is SignalAction.ENTER_LONG:
            pos, entry = 1, px
        elif action is SignalAction.ENTER_SHORT:
            pos, entry = -1, px
        elif action is SignalAction.EXIT and pos:
            total += pos * (px - entry) / entry * 100.0
            pos = 0
    if pos:
        total += pos * (closes[-1] - entry) / entry * 100.0
    return total


def test_adaptive_beats_legacy_on_trend_then_chop_then_reversal():
    """The user's complaint — "the bot works monotonously and its equations are
    suspicious" — as an executable assertion.

    Synthetic path, no fees: this shows the mapping change fixed which way the
    strategy points and when it stays out. It is not evidence of live edge.
    """
    closes: list[float] = []
    px = 100.0
    for i in range(600):
        if i < 200:
            px *= 1.0 + 0.0008                       # uptrend
        elif i < 400:
            px *= 1.0 + 0.0009 * math.sin(i / 3.0)   # chop
        else:
            px *= 1.0 - 0.0008                       # downtrend
        closes.append(px)

    legacy = feed_bars(ConsensusStrategy(vote_mode="legacy"), "T", closes)
    adaptive = feed_bars(ConsensusStrategy(vote_mode="adaptive"), "T", closes)

    # legacy churns through the chop; adaptive rides the two trends and sits out
    assert len(legacy) > 4 * len(adaptive)
    assert _naive_directional_pnl(legacy, closes) < 0.0
    assert _naive_directional_pnl(adaptive, closes) > 10.0


# ---- direction filter / confirmation / trail / hold -----------------------


def test_direction_filter_blocks_countertrend_bounce():
    """A sharp bounce inside a downtrend can flip the bar-level votes long; the
    multi-bar EMA slope is still down, so the direction filter must refuse it."""
    down = path_trend(5, direction=-1, n=100)
    rng = random.Random(77)
    px, bounce = down[-1], []
    for _ in range(12):  # ~4.9% bounce: flips the fast votes, not the 20-bar slope
        px *= 1.0 + 0.004 + rng.uniform(-0.0005, 0.0005)
        bounce.append(px)
    closes = down + bounce
    plain = ConsensusStrategy(symbols=("SPY",))
    filtered = ConsensusStrategy(symbols=("SPY",), direction_bars=20)
    plain_events = [a for _, a in feed_bars(plain, "SPY", closes)]
    filtered_events = [a for _, a in feed_bars(filtered, "SPY", closes)]
    assert SignalAction.ENTER_LONG in plain_events
    assert SignalAction.ENTER_LONG not in filtered_events


def test_confirm_bars_delays_entry():
    """confirm_bars=N requires N consecutive qualifying bars: the entry must
    fire strictly later than with confirm_bars=1 (same market shape)."""
    closes = path_trend(3, direction=1)
    quick = ConsensusStrategy(symbols=("SPY",), confirm_bars=1)
    slow = ConsensusStrategy(symbols=("SPY",), confirm_bars=4)
    q_events = feed_bars(quick, "SPY", closes)
    s_events = feed_bars(slow, "SPY", closes)
    assert [a for _, a in q_events] == [SignalAction.ENTER_LONG]
    assert [a for _, a in s_events] == [SignalAction.ENTER_LONG]
    assert s_events[0][0] > q_events[0][0]


def test_trail_exit_after_retrace():
    """A rise to a peak, then a pullback beyond trail_pct from the peak: the
    trail exit must close the position while the score may not have flipped."""
    closes = path_trend(3, direction=1, n=120)
    rng = random.Random(9)
    px, down = closes[-1], []
    for _ in range(60):  # give back ~1.5% after the peak
        px *= 1.0 - 0.00025 + rng.uniform(-0.0003, 0.0003)
        down.append(px)
    path = closes + down
    strat = ConsensusStrategy(symbols=("SPY",), exit_mode="trail", trail_pct=0.004)
    events = feed_bars(strat, "SPY", path)
    actions = [a for _, a in events]
    assert SignalAction.ENTER_LONG in actions
    assert SignalAction.EXIT in actions
    exit_bar = next(b for b, a in events if a is SignalAction.EXIT)
    entry_bar = events[0][0]
    assert exit_bar > entry_bar


def test_hold_mode_never_exits_on_score():
    """exit_mode='hold' leaves position management to the risk layer: the
    strategy must emit ENTER but never a score-driven EXIT."""
    closes = path_reversal(7)
    strat = ConsensusStrategy(symbols=("SPY",), exit_mode="hold")
    events = feed_bars(strat, "SPY", closes)
    actions = [a for _, a in events]
    assert SignalAction.ENTER_LONG in actions
    assert SignalAction.EXIT not in actions


# ---- trail anchor lifecycle (peak/last_close reset) -------------------------
#
# The trail band (`px <= peak*(1-trail_pct)` for longs) is only as good as the
# anchor: a trade must anchor at ITS OWN high-water mark, accumulated from the
# first completed bar (even while min_hold_bars still gates the exit decision).
# These scenarios use a two-vote weight map (EMA + RSI) so the MACD histogram —
# which sours for a dozen bars after any dip — cannot gate the re-entry timing.


def _two_long_trades_path() -> tuple[list[float], float]:
    """Ramp -> entry -> one-bar dip that pierces a 0.5% trail band -> flat
    bottom -> fresh ramp. The re-entry fires while price is still BELOW the
    first trade's trail band, with no short in between (the dip is too shallow
    to qualify one)."""
    closes: list[float] = []
    px = 100.0
    for _ in range(45):
        px *= 1.003
        closes.append(px)
    peak = px
    for f in (0.994, 0.999, 0.999, 1.0):
        px *= f
        closes.append(px)
    for _ in range(25):
        px *= 1.003
        closes.append(px)
    return closes, peak


def test_trail_anchor_resets_on_re_entry():
    """A second long must anchor its trail at ITS entry region, not the first
    trade's high-water mark P.

    The re-entry happens below the first trade's trail band, so under the
    stale-peak bug the very first decision bar of trade 2 would judge
    ``close <= P*(1-trail_pct)`` true and exit immediately. With the anchor
    reset at entry, the second trade rides the fresh ramp."""
    closes, peak = _two_long_trades_path()
    strat = ConsensusStrategy(
        symbols=("SPY",), exit_mode="trail", trail_pct=0.005,
        min_hold_bars=0, cooldown_bars=0, confirm_bars=1,
        weights={"ema": 0.5, "rsi": 0.5},
    )
    events = feed_bars(strat, "SPY", closes)
    assert [a for _, a in events] == [
        SignalAction.ENTER_LONG, SignalAction.EXIT, SignalAction.ENTER_LONG,
    ]
    _, exit_bar, reentry_bar = (b for b, _ in events)
    assert exit_bar == reentry_bar - 1  # re-entry on the bar after the trail exit
    # ... below the first trade's trail band: the bug would exit right here
    assert closes[reentry_bar] < peak * (1.0 - 0.005)
    # the flat bottom bar that follows is what the stale anchor would judge —
    # it is below the band, so the bug's immediate exit triggers exactly here
    assert closes[reentry_bar + 1] <= peak * (1.0 - 0.005)
    assert strat._states["SPY"].direction == 1


def test_on_position_closed_resets_trail_anchor():
    """A mechanical stop/take-profit must not leave the stale high-water mark
    behind for the next trade (the runner re-arms the strategy through this
    hook when the risk layer closes a position the strategy did not ask to)."""
    closes = path_trend(3, direction=1, n=60)
    strat = ConsensusStrategy(
        symbols=("SPY",), exit_mode="trail", trail_pct=0.005,
        min_hold_bars=0, confirm_bars=1, weights={"ema": 0.5, "rsi": 0.5},
    )
    events = feed_bars(strat, "SPY", closes)
    assert [a for _, a in events] == [SignalAction.ENTER_LONG]
    st = strat._states["SPY"]
    assert st.peak > 0.0 and st.last_close > 0.0  # anchors accumulated
    strat.on_position_closed("SPY", "stop")
    assert st.direction == 0
    assert st.peak == 0.0
    assert st.last_close == 0.0


def test_peak_accumulates_during_min_hold_and_anchors_first_trail_decision():
    """The trail anchor must accumulate from the FIRST completed bar of the
    trade, even though min_hold_bars gates the exit decision itself.

    The run-up to the trade's high-water mark happens entirely inside the
    min_hold window; the crash below the trail band happens while min_hold is
    still active and price then goes flat. The exit must therefore fire on the
    VERY FIRST post-min-hold decision bar — which is only possible if the peak
    includes the pre-min-hold run-up (a peak tracked from min_hold onward would
    sit at the flat price and never exit)."""
    closes: list[float] = []
    px = 100.0
    for _ in range(35):  # the entry fires on the earliest bar the indicators allow
        px *= 1.003
        closes.append(px)
    px *= 1.01  # trade bar 1: run-up to the trade high while min_hold is active
    trade_high = px
    closes.append(px)
    px *= 0.95  # trade bar 2: crash below the trail band, still inside min_hold
    closes.append(px)
    for _ in range(15):  # flat below the band for the rest of the path
        px *= 1.0005
        closes.append(px)

    strat = ConsensusStrategy(
        symbols=("SPY",), exit_mode="trail", trail_pct=0.02,
        min_hold_bars=5, cooldown_bars=0, confirm_bars=1,
        weights={"ema": 0.5, "rsi": 0.5},
    )
    events = feed_bars(strat, "SPY", closes)
    assert [(b, a) for b, a in events] == [
        (35, SignalAction.ENTER_LONG), (40, SignalAction.EXIT),
    ]
    entry_bar, exit_bar = (b for b, _ in events)
    assert exit_bar - entry_bar == 5  # the FIRST decision bar min_hold allows
    # the anchor was the pre-crash high accumulated during min_hold
    assert strat._states["SPY"].peak == pytest.approx(trade_high)


# ---- T3 accuracy levers: long_only, time stop, entry sigma ------------------


def feed_signals(strat: ConsensusStrategy, symbol: str,
                 closes: list[float]) -> list[tuple[int, Signal]]:
    """One tick per 5s bucket; returns (bar_index, Signal) pairs."""
    out: list[tuple[int, Signal]] = []
    for i, px in enumerate(closes):
        ts = i * _BAR_NS + 1
        for sig in strat.on_tick(symbol, px, ts, events=[]):
            out.append((i, sig))
    return out


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_long_only_never_emits_enter_short(seed):
    """A short-qualifying score is skipped entirely under long_only: the
    downtrend that produces exactly one ENTER_SHORT normally must produce
    NOTHING, and the strategy must not secretly track a short either."""
    strat = ConsensusStrategy(symbols=("SPY",), long_only=True)
    events = feed_signals(strat, "SPY", path_trend(seed, direction=-1))
    assert events == []
    assert strat._states["SPY"].direction == 0
    # no short streak survives either: the reset leaves it at zero
    assert strat._states["SPY"].streak == 0


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_long_only_still_enters_long(seed):
    strat = ConsensusStrategy(symbols=("SPY",), long_only=True)
    events = feed_signals(strat, "SPY", path_trend(seed, direction=1))
    assert [sig.action for _, sig in events] == [SignalAction.ENTER_LONG]


def test_long_only_default_false_keeps_shorts():
    """Default off: existing behavior bit-for-bit (the short still fires)."""
    strat = ConsensusStrategy(symbols=("SPY",))
    events = feed_signals(strat, "SPY", path_trend(7, direction=-1))
    assert [sig.action for _, sig in events] == [SignalAction.ENTER_SHORT]


def test_time_stop_fires_at_the_configured_bar():
    """max_hold_bars=3 with exit_mode='hold' (which never exits on score):
    the only possible EXIT is the time stop, exactly 3 completed bars in,
    overriding the score/trend/trail checks by construction."""
    closes = path_trend(7, direction=1, n=60)
    strat = ConsensusStrategy(
        symbols=("SPY",), exit_mode="hold", max_hold_bars=3, min_hold_bars=3,
        confirm_bars=1, cooldown_bars=1 << 30,  # no re-entry: exactly one trade
    )
    events = feed_signals(strat, "SPY", closes)
    assert [sig.action for _, sig in events] == [
        SignalAction.ENTER_LONG, SignalAction.EXIT,
    ]
    entry_bar, exit_bar = (b for b, _ in events)
    assert exit_bar - entry_bar == 3
    assert "time stop" in events[1][1].reason
    st = strat._states["SPY"]
    assert st.direction == 0 and st.bars_in_trade == 0


def test_time_stop_is_off_by_default():
    """max_hold_bars defaults to 0: the same hold-mode path never exits."""
    strat = ConsensusStrategy(symbols=("SPY",), exit_mode="hold")
    events = feed_signals(strat, "SPY", path_trend(7, direction=1, n=120))
    assert [sig.action for _, sig in events] == [SignalAction.ENTER_LONG]


def test_time_stop_validation_requires_min_hold_compatibility():
    with pytest.raises(ValueError):
        ConsensusStrategy(symbols=("SPY",), min_hold_bars=5, max_hold_bars=3)
    with pytest.raises(ValueError):
        ConsensusStrategy(symbols=("SPY",), max_hold_bars=-1)
    # 0 (off) and >= min_hold are both legal
    ConsensusStrategy(symbols=("SPY",), min_hold_bars=5, max_hold_bars=0)
    ConsensusStrategy(symbols=("SPY",), min_hold_bars=5, max_hold_bars=5)


def test_entry_signals_carry_entry_bar_sigma():
    """ENTRY signals carry the entry bar's RMS of returns (a fraction) and the
    cost-gate cache is set to the same value; EXIT signals carry no sigma."""
    closes = path_trend(3, direction=1, n=60) + [
        px * 0.97 for px in path_trend(3, direction=1, n=60)[-60:]
    ]
    strat = ConsensusStrategy(
        symbols=("SPY",), exit_mode="trail", trail_pct=0.005,
        min_hold_bars=0, cooldown_bars=0, confirm_bars=1,
        weights={"ema": 0.5, "rsi": 0.5},
    )
    events = feed_signals(strat, "SPY", closes)
    actions = [sig.action for _, sig in events]
    assert SignalAction.ENTER_LONG in actions
    assert SignalAction.EXIT in actions
    entry = [sig for _, sig in events if sig.action is SignalAction.ENTER_LONG][-1]
    exit_sig = next(sig for _, sig in events if sig.action is SignalAction.EXIT)
    assert entry.sigma is not None and entry.sigma > 0.0
    assert entry.sigma < 1.0  # a fraction, not a percent
    # the cache holds the LAST entry's sigma (no cost model -> no other writer)
    assert strat._last_move_rms["SPY"] == pytest.approx(entry.sigma)
    assert exit_sig.sigma is None


def test_config_wiring_long_only_and_max_hold_bars():
    from entropy.bot.config import ConsensusConfig

    cfg = BotConfig(strategies=("consensus",), consensus=ConsensusConfig(
        long_only=True, max_hold_bars=9,
    ))
    strat = build_strategies(cfg)[0]
    assert strat.long_only is True
    assert strat.max_hold_bars == 9
    # defaults ship the verified "H" config (PROJECT.md, WR > 60% OOS):
    # long_only on, 96-bar time stop, 20-bar direction filter
    dflt = build_strategies(BotConfig())[0]
    assert dflt.long_only is True and dflt.max_hold_bars == 96
    assert dflt.direction_bars == 20


def test_default_config_is_shipped_h():
    """The bare BotConfig() default IS the shipped "H" configuration verified
    OOS on 2026-09-03 (long_only + direction_bars=20 + max_hold_bars=96 +
    sigma 5.0/4.0 barriers) — see PROJECT.md "Win rate > 60% OOS"."""
    cfg = BotConfig()
    c = cfg.consensus
    assert (c.long_only, c.direction_bars, c.max_hold_bars) == (True, 20, 96)
    assert c.exit_mode == "trail" and c.trail_pct == 0.3
    assert c.threshold == 0.5 and c.confirm_bars == 2
    assert c.min_hold_bars == 5 and c.cooldown_bars == 4
    assert c.move_floor == 0.0003 and cfg.cost_edge_mult == 1.0
    ro = cfg.risk_overrides
    assert (ro.stop_mode, ro.stop_sigma_mult, ro.tp_sigma_mult) == ("sigma", 5.0, 4.0)
    assert validate(cfg) == []
