"""BlackScholesStrategy: deterministic synthetic paths, no network.

`test_pure_chop_produces_no_entry` is the regression that matters. The consensus
strategy once fired zero signals across a clean +44% trend and 36 signals across
flat chop — precisely inverted — because its vote mapping asked the oscillators a
question the regime made meaningless. A score built on N(d2) cannot repeat that
particular bug, but it can be broken the same WAY, so the shape of the test is
kept: one clean trend, one dead chop, and an assertion in both directions.
"""

from __future__ import annotations

import math

import pytest

from entropy.bot.signals import SignalAction
from entropy.bot.strategies.black_scholes import BlackScholesStrategy
from entropy.quant.conventions import for_symbol
from entropy.quant.vol import RealizedVolSource

_NS = 1_000_000_000
_BAR_S = 60.0
_BAR_NS = int(_BAR_S * _NS)

CRYPTO = "binance-spot:BTCUSDT"
EQUITY = "SPY"


def feed(strat, symbol, closes, start_bucket=0, ticks_per_bar=1):
    """`ticks_per_bar` ticks per bucket; (bar_index, action, signal) per signal.

    More than one tick per bucket is what separates a counter advanced on
    completed bars from one advanced on every tick: at one tick per bucket the
    two are the same stream, so a bar-counter bug is invisible. The offsets stay
    inside the bucket because `_BAR_NS` is 60e9 and `t` is a handful of ns.
    """
    out = []
    for i, px in enumerate(closes):
        ts = (start_bucket + i) * _BAR_NS
        for t in range(ticks_per_bar):
            for sig in strat.on_tick(symbol, px, ts + t, ()):
                out.append((i, sig.action, sig))
    return out


def trend(n: int, start: float = 100.0, per_bar: float = 0.0015) -> list[float]:
    """A clean, steady rise — the shape the old consensus mapping went blind to."""
    return [start * math.exp(per_bar * i) for i in range(n)]


def chop(n: int, start: float = 100.0, amp: float = 0.0008) -> list[float]:
    """Alternating moves with zero net displacement."""
    return [start * math.exp(amp * (1 if i % 2 == 0 else -1)) for i in range(n)]


def drifty(per_bars, start: float = 100.0, amp: float = 3.45e-4) -> list[float]:
    """Alternating +-`amp` chop carrying a per-bar drift that may change mid-path.

    Built as one continuous series — a concatenation of two separately generated
    paths would break the alternation at the seam and inject a spurious return.
    The two-sided movement holds realized sigma near 0.50 annualized, an order of
    magnitude above `RealizedVolSource`'s 0.05 floor, so the score below is a
    function of the drift rather than of a floored sigma.
    """
    out, level = [], math.log(start)
    for i, per_bar in enumerate(per_bars):
        level += per_bar
        out.append(math.exp(level + amp * (1 if i % 2 == 0 else -1)))
    return out


def make(symbol=CRYPTO, **kw):
    kw.setdefault("symbols", (symbol,))
    kw.setdefault("bar_s", _BAR_S)
    return BlackScholesStrategy(**kw)


def test_clean_uptrend_opens_a_long():
    strat = make()
    signals = feed(strat, CRYPTO, trend(200))
    entries = [s for _, a, s in signals if a is SignalAction.ENTER_LONG]
    assert entries, "a steady rise must produce at least one long"
    assert entries[0].strategy == "black_scholes"
    assert 0.0 < entries[0].strength <= 1.0


def test_clean_downtrend_opens_a_short():
    strat = make()
    signals = feed(strat, CRYPTO, trend(200, per_bar=-0.0015))
    assert any(a is SignalAction.ENTER_SHORT for _, a, _ in signals)


def test_pure_chop_produces_no_entry():
    strat = make()
    signals = feed(strat, CRYPTO, chop(200))
    entries = [a for _, a, _ in signals
               if a in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT)]
    assert entries == []


def test_zero_shrinkage_silences_the_strategy():
    """With shrinkage 0 the drift IS the carry, so the score is identically 0."""
    strat = make(drift_shrinkage=0.0)
    assert feed(strat, CRYPTO, trend(200)) == []
    assert strat.last_score[CRYPTO] == pytest.approx(0.0, abs=1e-12)


VIOLENT = chop(120) + [100.0 * 10.0] + chop(40)


def peak_abs_score(strat, symbol, closes):
    """The largest |score| the strategy reaches anywhere along `closes`.

    The final score is the wrong observable on a path that ends in chop — it
    decays back to 0.0 and hides everything the spike did. The peak is what the
    spike actually produced.
    """
    peak = 0.0
    for i, px in enumerate(closes):
        strat.on_tick(symbol, px, i * _BAR_NS, ())
        peak = max(peak, abs(strat.last_score.get(symbol, 0.0)))
    return peak


def test_one_violent_bar_cannot_saturate_the_score():
    """A 10x bar moves the score to 0.128 — nowhere near its ceiling.

    The bounds this test used to carry, `<= 1.0` and `< 0.999`, were unreachable:
    the score's supremum is ~0.5, so no implementation could fail them. Measured
    here the peak |score| is 0.1278 and the final is 0.0.

    The reason it cannot saturate is worth recording, because it is also why
    `drift_cap_sigmas` does nothing on this path (see the test below): the spike
    inflates sigma from 1.16 to 409 just as hard as it inflates the drift, and
    the score divides the one by the other, so the normalization absorbs it.
    Pinning the peak fails if a single bar is ever allowed to dominate.
    """
    strat = make()
    peak = peak_abs_score(strat, CRYPTO, VIOLENT)
    assert peak == pytest.approx(0.1278, abs=5e-4)
    assert strat.last_score[CRYPTO] == pytest.approx(0.0, abs=1e-9)


def test_drift_cap_sigmas_reaches_the_shrinkage():
    """Two strategies differing ONLY in `drift_cap_sigmas` must score differently.

    What this guards is narrow and worth naming: that the strategy actually
    threads `drift_cap_sigmas` into `shrink_drift`. The cap's arithmetic is
    already pinned at unit level by `test_cap_bounds_a_violent_drift` in
    `tests/quant/test_distribution.py`; a wiring bug here — stored but never
    passed, or passed in the wrong slot — would otherwise be invisible to the
    whole suite.

    It is invisible at the DEFAULT cap because the cap never binds on any fixture
    in this file. `shrink_drift` limits |mu - carry| to `cap_sigmas * sigma/sqrt(T)`,
    which scales with sigma, and the 10x bar inflates sigma alongside the drift:
    at the spike |mu| is 30245 against a limit of 162434, so the cap is 5x away
    from binding and `drift_cap_sigmas=1e12` reproduces the default byte for byte.
    Binding needs cap_sigmas below 0.5586, measured. Hence 0.25 here, where the
    cap does bind and the peak score drops from 0.1278 to 0.0507.
    """
    loose = peak_abs_score(make(drift_cap_sigmas=3.0), CRYPTO, VIOLENT)
    tight = peak_abs_score(make(drift_cap_sigmas=0.25), CRYPTO, VIOLENT)
    assert loose == pytest.approx(0.1278, abs=5e-4)
    assert tight == pytest.approx(0.0507, abs=5e-4)
    assert tight < loose


def test_no_signal_before_min_bars():
    strat = make(min_bars=60)
    assert feed(strat, CRYPTO, trend(59)) == []


def test_entry_carries_risk_hints():
    strat = make()
    entries = [s for _, a, s in feed(strat, CRYPTO, trend(200))
               if a is SignalAction.ENTER_LONG]
    assert entries
    first = entries[0]
    assert first.stop_pct is not None and first.stop_pct > 0.0
    assert first.tp_pct == pytest.approx(2.0 * first.stop_pct)
    assert first.size_pct is not None and 0.0 < first.size_pct <= 100.0


def test_exit_hints_are_absent():
    """Only entries price a stop; an exit closes at the mark."""
    strat = make(min_hold_bars=0, threshold=0.02)
    signals = feed(strat, CRYPTO, trend(160) + chop(120, start=trend(160)[-1]))
    exits = [s for _, a, s in signals if a is SignalAction.EXIT]
    assert exits
    assert all(s.stop_pct is None and s.tp_pct is None and s.size_pct is None
               for s in exits)


def test_min_hold_bars_delays_the_exit():
    """The exit lands where min_hold expires, not where the score happens to decay.

    The gap is asserted EXACTLY, and the trend is cut short so that min_hold is
    the binding constraint. On `trend(41)` the score-driven exit falls at bar 61,
    a gap of 21; a gap of 30 can therefore only mean min_hold held the position
    the extra 9 bars. A lower bound here would not test min_hold at all — against
    `trend(160)` the natural gap is 140 bars, so `>= 8` holds at every value of
    min_hold from 0 to 60 and passes even with the check deleted outright.

    Three ticks per bar additionally separate a counter advanced per completed
    bar from one advanced per tick; the latter reaches 30 three times too early
    and the exit falls back to the score-driven bar 61.
    """
    strat = make(min_hold_bars=30, threshold=0.02)
    closes = trend(41)
    signals = feed(strat, CRYPTO, closes + chop(160, start=closes[-1]),
                   ticks_per_bar=3)
    entry_i = next(i for i, a, _ in signals if a is SignalAction.ENTER_LONG)
    exit_i = next(i for i, a, _ in signals if a is SignalAction.EXIT)
    assert exit_i - entry_i == 30


def test_cooldown_delays_re_entry():
    """Re-entry lands where the cooldown expires, not where the score recovers.

    Same discipline as the min_hold test: the score-driven re-entry falls 42 bars
    after the exit, so a cooldown of 60 binds and the gap is asserted exactly. At
    the original 25 the cooldown was invisible — every value from 0 to 42 gives a
    gap of 42, so `>= 25` passed even with the cooldown check deleted.
    """
    strat = make(cooldown_bars=60, min_hold_bars=0, threshold=0.02)
    closes = trend(160)
    signals = feed(strat, CRYPTO, closes + chop(60, start=closes[-1])
                   + trend(160, start=closes[-1]), ticks_per_bar=3)
    exit_i = next(i for i, a, _ in signals if a is SignalAction.EXIT)
    later = [i for i, a, _ in signals
             if a is SignalAction.ENTER_LONG and i > exit_i]
    assert later, "the second trend must eventually re-enter"
    assert later[0] - exit_i == 60


def test_exit_band_is_half_the_threshold():
    """A score decaying into (threshold/2, threshold) must NOT close the trade.

    This is the only test that tells the hysteresis band apart from the entry
    threshold. The path enters at a score of ~0.0499 and settles at ~0.0150 —
    under the 0.02 threshold but over the 0.01 band — so a strategy exiting at
    `threshold` rather than `threshold / 2` closes here while a correct one
    holds. Without it, widening the band to the full threshold changes nothing
    any test in this file can see.
    """
    strat = make(min_hold_bars=0, threshold=0.02)
    signals = feed(strat, CRYPTO, drifty([5.2e-5] * 120 + [1.56e-5] * 160))
    assert any(a is SignalAction.ENTER_LONG for _, a, _ in signals)
    assert [s for _, a, s in signals if a is SignalAction.EXIT] == []
    # Guards the premise: a floored sigma would make the score mean something else.
    assert strat.last_sigma[CRYPTO] > 0.4
    assert 0.01 < strat.last_score[CRYPTO] < 0.02


def test_on_position_closed_rearms_and_starts_the_cooldown():
    strat = make()
    feed(strat, CRYPTO, trend(200))
    assert strat._states[CRYPTO].direction != 0
    strat.on_position_closed(CRYPTO, "take_profit")
    assert strat._states[CRYPTO].direction == 0
    assert strat._states[CRYPTO].bars_since_exit == 0


def test_unknown_exit_mode_is_rejected():
    with pytest.raises(ValueError, match="exit_mode"):
        make(exit_mode="whatever")


def test_symbols_filter_is_honoured():
    strat = make(symbols=(CRYPTO,))
    assert feed(strat, EQUITY, trend(200)) == []


def test_each_market_gets_its_own_convention():
    strat = BlackScholesStrategy(symbols=None, bar_s=_BAR_S)
    feed(strat, CRYPTO, trend(200))
    feed(strat, EQUITY, trend(200), start_bucket=1000)
    assert strat.convention_for(CRYPTO).bars_per_year == pytest.approx(
        for_symbol(CRYPTO, _BAR_S).bars_per_year
    )
    assert strat.convention_for(EQUITY).bars_per_year == pytest.approx(
        for_symbol(EQUITY, _BAR_S).bars_per_year
    )
    assert strat.convention_for(CRYPTO).bars_per_year != pytest.approx(
        strat.convention_for(EQUITY).bars_per_year
    )


def test_warmup_seeds_closes_so_the_first_live_bar_can_signal():
    from entropy.strategy.engine import Bar

    closes = trend(120)
    bars = [Bar(ts_ns=i * _BAR_NS, close=c, high=c, low=c)
            for i, c in enumerate(closes)]
    strat = make(min_bars=40)
    strat.warmup(bars)
    signals = feed(strat, CRYPTO, trend(6, start=closes[-1]), start_bucket=len(closes))
    assert signals, "a warmed strategy must be able to signal within a few bars"


def test_unavailable_vol_source_blocks_signals_and_says_why():
    class Silent:
        name = "silent"
        last_reason = "no options catalog configured"

        def sigma(self, symbol, closes, conv):
            return None

    strat = make(vol_source=Silent())
    assert feed(strat, CRYPTO, trend(200)) == []
    assert "catalog" in strat.last_rejects[CRYPTO]


def test_realized_source_is_the_default():
    assert isinstance(make().vol_source, RealizedVolSource)


def test_reason_string_reports_score_sigma_and_market():
    strat = make()
    entries = [s for _, a, s in feed(strat, CRYPTO, trend(200))
               if a is SignalAction.ENTER_LONG]
    assert entries
    reason = entries[0].reason
    assert reason.startswith("bs ")
    assert "crypto" in reason
    assert "σ" in reason
