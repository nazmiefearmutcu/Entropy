"""ConsensusStrategy: deterministic synthetic-path tests (no network).

Path shapes are tuned so the *intended market regime* produces the intended
behavior with the default vote thresholds: a deep dip keeps RSI oversold while
EMA/MACD lag through the turn (no spurious short), and a zigzag rise (net
+0.1%/bar) keeps RSI inside the 30-70 band so the trend votes can reach
consensus. Every path is seeded and fully deterministic.
"""

from __future__ import annotations

import random

import pytest

from entropy.bot.config import BotConfig, build_strategies
from entropy.bot.signals import SignalAction
from entropy.bot.strategies.consensus import (
    DEFAULT_WEIGHTS,
    ConsensusStrategy,
    Votes,
    _band_vote,
    _cmp_vote,
    _percent_b,
    _sign_vote,
    score_votes,
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


def path_uptrend(seed: int, scale: float = 1.0, cycles: int = 20) -> list[float]:
    """Flat base -> deep dip -> flat base -> zigzag uptrend (net ~+0.1%/bar)."""
    rng = random.Random(seed)
    px, closes = 100.0, []
    for _ in range(40):  # quiet flat base
        px *= 1.0 + scale * rng.uniform(-0.0004, 0.0004)
        closes.append(px)
    for _ in range(12):  # dip: sets RSI oversold so the turn can't fake a short
        px *= 1.0 + scale * (-0.002 + rng.uniform(-0.0002, 0.0002))
        closes.append(px)
    for _ in range(8):  # base: lets EMA/MACD gaps decay before the rise
        px *= 1.0 + scale * rng.uniform(-0.0003, 0.0003)
        closes.append(px)
    for _ in range(cycles):  # zigzag rise keeps RSI < 70 (RS = 2 -> RSI ~ 66)
        for r in (0.003, 0.003, -0.003):
            px *= 1.0 + scale * (r + rng.uniform(-0.0002, 0.0002))
            closes.append(px)
    return closes


def path_reversal(seed: int) -> list[float]:
    """Uptrend (14 zig cycles) then a mirrored zigzag decline (16 cycles)."""
    closes = path_uptrend(seed, cycles=14)
    rng = random.Random(seed + 10_000)
    px = closes[-1]
    for _ in range(16):
        for r in (-0.003, -0.003, 0.003):
            px *= 1.0 + r + rng.uniform(-0.0002, 0.0002)
            closes.append(px)
    return closes


def path_chop(seed: int, n: int = 120) -> list[float]:
    """Flat chop: +-0.02% noise around 100."""
    rng = random.Random(seed)
    return [100.0 * (1.0 + rng.uniform(-0.0002, 0.0002)) for _ in range(n)]


# ---- trend / hysteresis / regime ------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11, 42])
def test_uptrend_exactly_one_enter_long_no_churn(seed):
    strat = ConsensusStrategy(symbols=("SPY",))
    events = feed_bars(strat, "SPY", path_uptrend(seed))
    assert [a for _, a in events] == [SignalAction.ENTER_LONG]
    # hysteresis held the position through the rest of the trend
    assert strat._states["SPY"].direction == 1


@pytest.mark.parametrize("seed", [3, 7, 42])
def test_flat_chop_zero_entries(seed):
    strat = ConsensusStrategy(symbols=("SPY",))
    assert feed_bars(strat, "SPY", path_chop(seed)) == []


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_regime_filter_blocks_scaled_down_trend(seed):
    """Same shape scaled 10x down: votes are scale-invariant so the score still
    reaches the threshold, but realized vol/slope fall below the regime floor
    -> the regime filter alone must block every entry."""
    strat = ConsensusStrategy(symbols=("SPY",))
    assert feed_bars(strat, "SPY", path_uptrend(seed, scale=0.1)) == []


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11, 42])
def test_reversal_exit_then_enter_short(seed):
    strat = ConsensusStrategy(symbols=("SPY",))
    events = feed_bars(strat, "SPY", path_reversal(seed))
    assert [a for _, a in events] == [
        SignalAction.ENTER_LONG, SignalAction.EXIT, SignalAction.ENTER_SHORT,
    ]
    long_bar = events[0][0]
    exit_bar = events[1][0]
    short_bar = events[2][0]
    assert long_bar < exit_bar < short_bar
    assert strat._states["SPY"].direction == -1


def test_threshold_respected_no_entry_below():
    """Max consensus on this path is 0.65 (ema+ macd+ rsi0 bb0); raising the
    threshold above it must silence the strategy entirely."""
    strat = ConsensusStrategy(symbols=("SPY",), threshold=0.7)
    assert feed_bars(strat, "SPY", path_uptrend(7)) == []


# ---- warmup ----------------------------------------------------------------


def _bars(closes: list[float]) -> list[Bar]:
    return [Bar(ts_ns=i * _BAR_NS, close=c) for i, c in enumerate(closes)]


def test_warmup_split_matches_all_live_feed():
    closes = path_uptrend(7)
    warmed = ConsensusStrategy(symbols=("SPY",))
    warmed.warmup(_bars(closes[:60]))
    live = ConsensusStrategy(symbols=("SPY",))
    assert (feed_bars(warmed, "SPY", closes[60:], start_bucket=60)
            == feed_bars(live, "SPY", closes)
            == [(69, SignalAction.ENTER_LONG)])


def test_warmup_makes_strategy_immediately_eligible():
    """Warmed right up to the consensus bar: entry must come within the first
    few live bars, with no fresh min_bars (35-bar) wait."""
    closes = path_uptrend(7)
    strat = ConsensusStrategy(symbols=("SPY",))
    strat.warmup(_bars(closes[:69]))
    events = feed_bars(strat, "SPY", closes[69:80], start_bucket=69)
    assert events and events[0][1] is SignalAction.ENTER_LONG
    assert events[0][0] <= 69 + 5  # far below min_bars=35 live bars


def test_warmup_none_symbols_seeds_first_symbol_seen():
    closes = path_uptrend(7)
    strat = ConsensusStrategy()  # symbols=None -> all symbols
    strat.warmup(_bars(closes[:69]))
    events = feed_bars(strat, "BTCUSDT", closes[69:80], start_bucket=69)
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
def test_score_votes_table(votes, weights, expected):
    assert score_votes(votes, weights) == pytest.approx(expected)


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


def test_rsi_monotonic_series_is_overbought_vote():
    """Cross-check against the real indicator: a strictly rising series has
    RSI = 100, which must land in the -1 (overbought) vote zone."""
    from stockodile.analytics import calculate_rsi

    rsi = calculate_rsi([100.0 + i for i in range(30)], 14)
    assert rsi[-1] == pytest.approx(100.0)
    assert _band_vote(rsi[-1], low=30.0, high=70.0) == -1


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
    assert cfg.strategies == ("consensus", "ema_cross")
    strats = build_strategies(cfg)
    assert [s.name for s in strats] == ["consensus", "ema_cross"]
    assert isinstance(strats[0], ConsensusStrategy)


def test_momentum_scalper_still_buildable_by_name():
    cfg = BotConfig(strategies=("momentum_scalper", "consensus"))
    assert [s.name for s in build_strategies(cfg)] == ["momentum_scalper", "consensus"]


def test_ctor_validation():
    with pytest.raises(ValueError):
        ConsensusStrategy(bar_s=0.0)
    with pytest.raises(ValueError):
        ConsensusStrategy(threshold=0.0)
    with pytest.raises(ValueError):
        ConsensusStrategy(threshold=1.5)
