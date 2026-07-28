"""The arithmetic, and the proof that it is ONE arithmetic.

`test_bsm_and_black76_agree` is the load-bearing test of this whole layer: it
shows Black-Scholes-Merton on the spot with carry c and Black-76 on the forward
S*e^(cT) are the same number to machine precision. "Works for both markets" is
that equality, demonstrated — not a docstring claim.
"""

from __future__ import annotations

import math

import pytest
from crocodile.core.schema.enums import OptType
from crocodile.crypto.analytics.blackscholes import bs_price
from crocodile.equity.analytics.options import bsm_price

from entropy.quant.pricing import d1, d2, forward, norm_cdf, prob_above, prob_below


def test_textbook_call_and_put():
    # S=100, K=100, T=1, r=5%, sigma=20%, q=0 -> the standard worked example.
    call = bsm_price(100.0, 100.0, 1.0, 0.05, 0.20, 0.0, "call")
    put = bsm_price(100.0, 100.0, 1.0, 0.05, 0.20, 0.0, "put")
    assert call == pytest.approx(10.4506, rel=1e-4)
    assert put == pytest.approx(5.5735, rel=1e-4)


@pytest.mark.parametrize("k", [80.0, 100.0, 125.0])
@pytest.mark.parametrize("t", [0.08, 1.0, 2.5])
def test_put_call_parity(k, t):
    s, r, q, sigma = 100.0, 0.045, 0.02, 0.35
    call = bsm_price(s, k, t, r, sigma, q, "call")
    put = bsm_price(s, k, t, r, sigma, q, "put")
    assert call - put == pytest.approx(
        s * math.exp(-q * t) - k * math.exp(-r * t), abs=1e-9
    )


@pytest.mark.parametrize("k", [80.0, 100.0, 125.0])
@pytest.mark.parametrize("carry", [-0.05, 0.0, 0.04, 0.30])
def test_bsm_and_black76_agree(k, carry):
    """One arithmetic, two spellings: BSM on the spot == Black-76 on the forward."""
    s, t, sigma = 100.0, 0.5, 0.30
    spot_side = bsm_price(s, k, t, carry, sigma, 0.0, "call")
    fwd_side = bs_price(forward(s, carry, t), k, t, sigma, OptType.CALL, rate=carry)
    assert spot_side == pytest.approx(fwd_side, abs=1e-10)


@pytest.mark.parametrize("carry", [-0.05, 0.0, 0.04, 0.30])
def test_d2_matches_the_black76_forward_form(carry):
    s, k, t, sigma = 100.0, 112.0, 0.5, 0.30
    f = forward(s, carry, t)
    black76_d2 = (math.log(f / k) - 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    assert d2(s, k, t, sigma, carry) == pytest.approx(black76_d2, abs=1e-12)


def test_d1_and_d2_differ_by_one_sigma_root_t():
    s, k, t, sigma, c = 100.0, 105.0, 0.25, 0.42, 0.03
    assert d1(s, k, t, sigma, c) - d2(s, k, t, sigma, c) == pytest.approx(
        sigma * math.sqrt(t), abs=1e-12
    )


def test_forward_carries_the_spot():
    assert forward(100.0, 0.0, 1.0) == pytest.approx(100.0)
    assert forward(100.0, 0.10, 1.0) == pytest.approx(100.0 * math.exp(0.10))


def test_probabilities_are_complements():
    s, k, t, sigma, m = 100.0, 103.0, 0.4, 0.28, 0.06
    assert prob_above(s, k, t, sigma, m) + prob_below(s, k, t, sigma, m) == pytest.approx(
        1.0, abs=1e-12
    )


def test_probability_limits():
    s, t, sigma, m = 100.0, 0.5, 0.30, 0.0
    assert prob_above(s, 1e-6, t, sigma, m) == pytest.approx(1.0, abs=1e-9)
    assert prob_above(s, 1e9, t, sigma, m) == pytest.approx(0.0, abs=1e-9)


def test_higher_drift_raises_the_probability_of_an_up_move():
    s, k, t, sigma = 100.0, 104.0, 0.25, 0.30
    assert prob_above(s, k, t, sigma, 0.40) > prob_above(s, k, t, sigma, 0.0)


def test_norm_cdf_is_the_standard_normal():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (dict(spot=0.0, strike=100.0, t_years=1.0, sigma=0.2, drift=0.0), "spot"),
        (dict(spot=100.0, strike=0.0, t_years=1.0, sigma=0.2, drift=0.0), "strike"),
        (dict(spot=100.0, strike=100.0, t_years=0.0, sigma=0.2, drift=0.0), "t_years"),
        (dict(spot=100.0, strike=100.0, t_years=1.0, sigma=0.0, drift=0.0), "sigma"),
    ],
)
def test_degenerate_inputs_raise(kwargs, match):
    with pytest.raises(ValueError, match=match):
        d2(**kwargs)
