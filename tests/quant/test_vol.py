"""Realized volatility, annualized in each market's own clock.

`test_annualized_sigma_is_sampling_invariant` is the one that matters: the same
price process sampled at two bar lengths must report the same annual sigma. The
path is deterministic (alternating +-h log returns) so the assertion is exact
rather than statistical.
"""

from __future__ import annotations

import math
import random

import pytest

from entropy.quant.conventions import Market, for_symbol
from entropy.quant.vol import RealizedVolSource, ewma_variance, log_returns


def closes_from_returns(start: float, returns: list[float]) -> list[float]:
    out = [start]
    for r in returns:
        out.append(out[-1] * math.exp(r))
    return out


def test_log_returns_length_and_value():
    assert log_returns([100.0]) == []
    rets = log_returns([100.0, 110.0])
    assert len(rets) == 1
    assert rets[0] == pytest.approx(math.log(1.1))


def test_log_returns_ignores_non_positive_prices():
    assert log_returns([100.0, 0.0, 100.0]) == []


def test_ewma_variance_of_constant_magnitude_returns_is_exact():
    # r^2 is h^2 on every step, so the recursion is a fixed point at h^2.
    h = 0.002
    rets = [h if i % 2 == 0 else -h for i in range(64)]
    assert ewma_variance(rets, 0.94) == pytest.approx(h * h, rel=1e-12)


def test_ewma_variance_of_empty_is_zero():
    assert ewma_variance([], 0.94) == 0.0


def test_annualized_sigma_is_sampling_invariant():
    """One process, two bar lengths, one annual sigma.

    A 15x coarser bar accumulates 15x the variance, so its per-bar move is
    sqrt(15) larger while its bars_per_year is 15x smaller. Annualization must
    cancel those exactly.
    """
    h = 0.001
    fine = [h if i % 2 == 0 else -h for i in range(400)]
    coarse = [h * math.sqrt(15.0) * (1 if i % 2 == 0 else -1) for i in range(400)]

    src = RealizedVolSource()
    sigma_fine = src.sigma(
        "binance-spot:BTCUSDT", closes_from_returns(100.0, fine),
        for_symbol("binance-spot:BTCUSDT", 60.0),
    )
    sigma_coarse = src.sigma(
        "binance-spot:BTCUSDT", closes_from_returns(100.0, coarse),
        for_symbol("binance-spot:BTCUSDT", 900.0),
    )
    assert sigma_fine == pytest.approx(sigma_coarse, rel=1e-9)


def test_equity_and_crypto_annualize_differently():
    h = 0.001
    closes = closes_from_returns(100.0, [h if i % 2 == 0 else -h for i in range(200)])
    src = RealizedVolSource(floor=1e-9)
    crypto = src.sigma("binance-spot:BTCUSDT", closes, for_symbol("binance-spot:BTCUSDT", 60.0))
    equity = src.sigma("SPY", closes, for_symbol("SPY", 60.0))
    assert crypto is not None and equity is not None
    # 525600 vs 98280 bars per year -> sqrt ratio.
    assert crypto / equity == pytest.approx(math.sqrt(525_600.0 / 98_280.0), rel=1e-9)


def test_sigma_recovers_a_synthetic_gbm():
    rng = random.Random(20260729)
    conv = for_symbol("binance-spot:BTCUSDT", 60.0)
    target_annual = 0.60
    per_bar = target_annual / math.sqrt(conv.bars_per_year)
    closes = closes_from_returns(30_000.0, [rng.gauss(0.0, per_bar) for _ in range(2000)])
    got = RealizedVolSource(floor=1e-9).sigma("binance-spot:BTCUSDT", closes, conv)
    assert got is not None
    assert got == pytest.approx(target_annual, rel=0.30)


def test_too_few_returns_yields_none_with_a_reason():
    src = RealizedVolSource(min_returns=10)
    assert src.sigma("SPY", [100.0, 101.0], for_symbol("SPY", 60.0)) is None
    assert "10" in src.last_reason


def test_floor_binds_on_a_dead_tape():
    src = RealizedVolSource(floor=0.05)
    got = src.sigma("SPY", [100.0] * 50, for_symbol("SPY", 60.0))
    assert got == pytest.approx(0.05)


def test_constructor_rejects_bad_parameters():
    with pytest.raises(ValueError, match="lam"):
        RealizedVolSource(lam=1.0)
    with pytest.raises(ValueError, match="floor"):
        RealizedVolSource(floor=0.0)
    with pytest.raises(ValueError, match="min_returns"):
        RealizedVolSource(min_returns=0)


def test_market_is_read_from_the_convention_not_the_symbol():
    # The source never parses the symbol itself; the convention already decided.
    conv = for_symbol("SPY", 60.0)
    assert conv.market is Market.EQUITY
