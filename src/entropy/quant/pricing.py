"""d1, d2 and the barrier probabilities — the only Black-Scholes arithmetic Entropy owns.

Everything else comes from crocodile, which already ships both halves:
``crocodile.equity.analytics.options`` (Black-Scholes-Merton with a dividend
yield) and ``crocodile.crypto.analytics.blackscholes`` (Black-76 on the
forward). What crocodile does not export is ``d1``/``d2`` themselves — they are
computed inside the price functions — and those are precisely what a directional
spot bot needs, because ``N(d2)`` is the probability of finishing beyond a
barrier.

Writing a third copy of Black-Scholes was the alternative, and
``crocodile/core/analytics/volsurface.py`` documents what that costs: the crypto
and equity slippage modules were a fork of one function that drifted apart at
both ends until neither was a superset of the other.

Nothing here knows about markets. ``drift`` is whatever measure the caller wants
the probability under — the carry for the risk-neutral one, an estimated mu for
the physical one. Substituting one for the other is the entire signal.
"""

from __future__ import annotations

import math

from crocodile.crypto.analytics.blackscholes import norm_cdf, norm_pdf

__all__ = [
    "d1",
    "d2",
    "forward",
    "norm_cdf",
    "norm_pdf",
    "prob_above",
    "prob_below",
]


def _validate(spot: float, strike: float, t_years: float, sigma: float) -> None:
    if spot <= 0.0:
        raise ValueError(f"spot must be positive, got {spot!r}")
    if strike <= 0.0:
        raise ValueError(f"strike must be positive, got {strike!r}")
    if t_years <= 0.0:
        raise ValueError(f"t_years must be positive, got {t_years!r}")
    if sigma <= 0.0:
        raise ValueError(f"sigma must be positive, got {sigma!r}")


def forward(spot: float, carry: float, t_years: float) -> float:
    """F = S*e^(cT) — the price both models agree on.

    ``carry`` (and ``drift`` everywhere else in this module) is an annualized
    *continuously compounded* rate, which is what this exponential and the d1
    carry term assume; an APR quote must be converted by the caller.

    Black-76 is written on this; Black-Scholes-Merton reaches the same number by
    carrying the spot inside d1. ``tests/quant/test_pricing.py`` proves it.
    """
    if spot <= 0.0:
        raise ValueError(f"spot must be positive, got {spot!r}")
    if t_years < 0.0:
        raise ValueError(f"t_years must be non-negative, got {t_years!r}")
    return spot * math.exp(carry * t_years)


def d1(spot: float, strike: float, t_years: float, sigma: float, drift: float) -> float:
    """[ln(S/K) + (m + sigma^2/2)T] / (sigma*sqrt(T))."""
    _validate(spot, strike, t_years, sigma)
    return (math.log(spot / strike) + (drift + 0.5 * sigma * sigma) * t_years) / (
        sigma * math.sqrt(t_years)
    )


def d2(spot: float, strike: float, t_years: float, sigma: float, drift: float) -> float:
    """[ln(S/K) + (m - sigma^2/2)T] / (sigma*sqrt(T)) = d1 - sigma*sqrt(T)."""
    return d1(spot, strike, t_years, sigma, drift) - sigma * math.sqrt(t_years)


def prob_above(
    spot: float, strike: float, t_years: float, sigma: float, drift: float
) -> float:
    """P(S_T > K) under geometric Brownian motion with annualized ``drift``.

    ln S_T = ln S_0 + (m - sigma^2/2)T + sigma*sqrt(T)*Z, so the probability is
    N(d2) with the drift standing where the carry stands in the risk-neutral
    formula.
    """
    return norm_cdf(d2(spot, strike, t_years, sigma, drift))


def prob_below(
    spot: float, strike: float, t_years: float, sigma: float, drift: float
) -> float:
    """P(S_T < K) — the complement of :func:`prob_above`."""
    return norm_cdf(-d2(spot, strike, t_years, sigma, drift))
