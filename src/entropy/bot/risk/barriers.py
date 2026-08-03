"""Barrier-hitting probabilities for stop/take-profit pairs under GBM.

This is the Black-Scholes *machinery* without the Black-Scholes *formula*. The
bot trades spot and perpetuals, so there is no strike, no expiry and no
risk-neutral measure to price against — ``C = S·N(d1) - K·e^(-rT)·N(d2)`` has
nowhere to attach. What does attach is the model underneath it: log-prices as a
Brownian motion with drift, which turns a stop/target pair into a *probability*
rather than a ratio.

Why this matters here
---------------------
``RiskProfile`` states reward ratios ("0.5% stop / 2.0% target (4:1 ratio)")
but never the odds those ratios buy. Under driftless GBM the two are inverses:
a 4:1 payoff is a 1:4 chance, and the resulting expectation is *exactly zero*
before costs. Measured against the shipped presets:

===========  =====  ================  ==============  ====================
profile      R:R    P(target first)   EV (no fees)    EV @ 0.08% round-trip
===========  =====  ================  ==============  ====================
Frosty       4.0:1            20.2%       +0.0050%              -0.0750%
Medium       2.0:1            33.7%       +0.0100%              -0.0700%
Extreme      2.0:1            34.0%       +0.0399%              -0.0401%
===========  =====  ================  ==============  ====================

So every preset sits on the fair-odds line and fees push all three negative.
The entire edge has to come from drift — from the consensus signal actually
predicting direction — and nothing in the risk layer currently checks whether
it does. These functions let it be checked.

A second consequence, visible only once the odds are written down:
``RiskManager.stop_tp_prices`` scales stop *and* target by the same factor, and
hitting probabilities are **scale-invariant** under driftless GBM. Widening
both by 3x moves P(target first) from 20.2% to 20.6% — the volatility scaling
does not buy better odds. It buys a better fee-to-move ratio (fees fall from 4%
to 1.3% of the target) and a longer holding time, which is a real but different
benefit than the one the code comments imply.

Assumptions and where they break
--------------------------------
GBM assumes constant volatility, continuous paths and log-normal returns.
Crypto violates all three: volatility clusters, gaps jump straight through
stops, and tails are far heavier than log-normal. Treat every probability here
as an *optimistic* bound — the true P(stop first) is higher than computed,
because a jump can cross the stop without ever touching the intervening prices
the diffusion model has to walk through. Use ``sigma_from_returns`` with a
short EWMA half-life to at least track the clustering.

Nothing in this module is wired into the runner; it is a measurement layer.
"""

from __future__ import annotations

import math

_SQRT_2 = math.sqrt(2.0)
# Below this |theta| the drifted formula loses precision to catastrophic
# cancellation (both exponentials collapse toward 1); the driftless limit is
# accurate to better than 1e-9 there.
_DRIFT_EPS = 1e-9


def log_barriers(entry_px: float, stop_px: float, tp_px: float) -> tuple[float, float]:
    """Return ``(a, b)``: log-distances to target and stop, both positive.

    Direction-agnostic — a short's stop sits above entry and its target below,
    which the absolute values fold away. ``a`` is the favourable barrier.
    """
    if entry_px <= 0 or stop_px <= 0 or tp_px <= 0:
        raise ValueError("prices must be positive")
    return abs(math.log(tp_px / entry_px)), abs(math.log(stop_px / entry_px))


def sigma_from_returns(closes: list[float], *, halflife: float | None = None) -> float:
    """Per-bar volatility of log returns.

    This is the quantity GBM is defined in terms of, and it is *not* what the
    risk manager currently measures: ``std/mean`` over a window of raw prices
    is a price dispersion, dimensionally unrelated to a return volatility and
    not comparable across profiles with different window lengths.

    With ``halflife`` set, returns are EWMA-weighted (in bars) so recent
    volatility dominates — the cheapest available concession to the fact that
    crypto volatility clusters and a flat window lags every regime change.
    """
    if len(closes) < 3:
        return 0.0
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i] > 0 and closes[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    if halflife is None or halflife <= 0:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var)
    lam = 0.5 ** (1.0 / halflife)
    weights = [lam ** (len(rets) - 1 - i) for i in range(len(rets))]
    wsum = sum(weights)
    mean = sum(w * r for w, r in zip(weights, rets, strict=True)) / wsum
    var = sum(w * (r - mean) ** 2 for w, r in zip(weights, rets, strict=True)) / wsum
    return math.sqrt(var)


def prob_target_first(a: float, b: float, *, nu: float = 0.0, sigma: float = 1.0) -> float:
    """P(log-price reaches ``+a`` before ``-b``), drift ``nu`` per bar.

    ``nu`` is the *log* drift, i.e. ``mu - sigma**2/2`` for an arithmetic
    expected return ``mu``. With ``nu=0`` this collapses to gambler's ruin,
    ``b / (a + b)``, independent of sigma — volatility then sets *when* a
    barrier is hit (see :func:`expected_bars_to_exit`), never *which* one.
    """
    if a <= 0 or b <= 0:
        raise ValueError("barriers must be positive log-distances")
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    theta = 2.0 * nu / (sigma ** 2)
    if abs(theta) < _DRIFT_EPS:
        return b / (a + b)
    # Two algebraically identical forms; each is the numerically stable one on
    # its side of zero, keeping every exponent negative and bounded.
    if theta > 0:
        return -math.expm1(-theta * b) / -math.expm1(-theta * (a + b))
    return (math.exp(theta * (a + b)) - math.exp(theta * a)) / math.expm1(theta * (a + b))


def expected_bars_to_exit(a: float, b: float, sigma: float) -> float:
    """Expected bars until *either* barrier is hit, driftless case: ``a·b/sigma²``.

    The bot has no time stop: a position sits open until price reaches a
    barrier, which under GBM is guaranteed to happen eventually but says
    nothing about when. This gives the horizon the thesis was implicitly
    written against — hold materially longer than this and the entry signal has
    already been falsified by silence.
    """
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    return a * b / (sigma ** 2)


def prob_above(spot: float, level: float, sigma: float, bars: float, *, mu: float = 0.0) -> float:
    """P(price ≥ ``level`` after ``bars``) — the real-world twin of ``N(d2)``.

    Identical in form to Black-Scholes' exercise probability, with the
    risk-free rate replaced by the actual expected return. This answers
    "where could price plausibly be in 15 bars", which is the right sanity
    check on a take-profit level; :func:`prob_target_first` answers the
    different and more relevant question of which barrier is reached first.
    """
    if spot <= 0 or level <= 0:
        raise ValueError("prices must be positive")
    if sigma <= 0 or bars <= 0:
        raise ValueError("sigma and bars must be positive")
    d2 = (math.log(spot / level) + (mu - 0.5 * sigma ** 2) * bars) / (sigma * math.sqrt(bars))
    return 0.5 * math.erfc(-d2 / _SQRT_2)


def expected_value(a: float, b: float, *, nu: float = 0.0, sigma: float = 1.0,
                   cost: float = 0.0) -> tuple[float, float]:
    """Return ``(p_target, expected_return)`` in log terms, net of ``cost``.

    ``cost`` is the round-trip friction as a fraction (0.0008 for 0.04% taker
    fees both ways). It is subtracted unconditionally because it is paid on
    winners and losers alike — which is exactly why three fair-odds presets
    come out negative.
    """
    p = prob_target_first(a, b, nu=nu, sigma=sigma)
    return p, p * a - (1.0 - p) * b - cost


# ---------------------------------------------------------------------------
# TODO(nazmi): the policy decision — how the bot should *act* on these numbers.
#
# Everything above is descriptive: given barriers and a drift estimate, here is
# the probability and the expectation. What is missing is the rule that turns
# that into an accept/reject, and it is a genuine judgement call rather than a
# derivation, because it hinges on how much you trust the consensus score as a
# drift estimate.
#
# The consensus strategy emits a score in [-1, 1] (see strategies/consensus.py).
# Mapping it to a per-bar log drift `nu` is the crux:
#
#   - Linear (`nu = score * k * sigma`) is simple and keeps a weak signal weak,
#     but treats a 0.55 score as genuinely half as predictive as a 1.0 — the
#     score was never calibrated to claim that.
#   - Threshold (`nu = k * sigma` above some score, else 0) refuses to read
#     precision into an ordinal signal, at the cost of throwing away ranking
#     information the walk-forward calibrator could otherwise exploit.
#   - Regime-conditional `k` (trend vs range, from consensus.Regime) matches
#     the finding that the same score means different things in different
#     regimes — richer, but another free parameter to overfit.
#
# And then the gate itself: reject on `ev <= 0`, or demand a margin (`ev > cost`,
# so a trade must beat its own friction by a clear multiple)? A hard zero
# threshold will approve trades whose edge is smaller than the drift estimate's
# own error bar.
#
# def should_enter(score: float, a: float, b: float, sigma: float,
#                  *, cost: float = 0.0008) -> tuple[bool, float, str]:
#     """Approve/reject an entry on expected value. Returns (ok, ev, reason)."""
#     ...
# ---------------------------------------------------------------------------
