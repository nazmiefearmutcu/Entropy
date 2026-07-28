"""MarketConvention: the one object that knows crypto from equities.

Everything downstream (sigma annualization, T in years, barrier distance, stop
distance) derives from `bars_per_year` and `carry`, so these tests are the whole
market-dependence surface of the Black-Scholes layer.
"""

from __future__ import annotations

import pytest

from entropy.quant.conventions import (
    EQUITY_SESSIONS_PER_YEAR,
    SECONDS_PER_CALENDAR_YEAR,
    SECONDS_PER_EQUITY_SESSION,
    Market,
    bars_per_year,
    for_symbol,
    market_of,
)


def test_venue_qualified_symbols_are_crypto():
    assert market_of("binance-spot:BTCUSDT") is Market.CRYPTO
    assert market_of("coinbase:BTC-USD") is Market.CRYPTO


def test_bare_tickers_are_equities():
    assert market_of("SPY") is Market.EQUITY
    assert market_of("BRK.B") is Market.EQUITY


def test_crypto_year_is_continuous():
    # 365 * 24 * 60 one-minute bars.
    assert bars_per_year(Market.CRYPTO, 60.0) == pytest.approx(525_600.0)


def test_equity_year_is_sessions_not_calendar():
    # 252 sessions * 390 one-minute bars per 6.5h session.
    assert bars_per_year(Market.EQUITY, 60.0) == pytest.approx(98_280.0)
    # The distinction is the point: a calendar year would be 5.35x larger.
    assert bars_per_year(Market.EQUITY, 60.0) < bars_per_year(Market.CRYPTO, 60.0)


def test_bars_per_year_scales_inversely_with_bar_length():
    for market in (Market.CRYPTO, Market.EQUITY):
        assert bars_per_year(market, 60.0) == pytest.approx(
            15.0 * bars_per_year(market, 900.0)
        )


def test_bar_length_must_be_positive():
    with pytest.raises(ValueError, match="bar_s"):
        bars_per_year(Market.CRYPTO, 0.0)


def test_equity_carry_is_rate_minus_dividend():
    conv = for_symbol("SPY", 60.0, risk_free_rate=0.04, dividend_yield=0.015)
    assert conv.market is Market.EQUITY
    assert conv.carry == pytest.approx(0.025)
    assert conv.model == "bsm"


def test_crypto_carry_is_the_configured_constant():
    conv = for_symbol("binance-spot:BTCUSDT", 60.0, crypto_carry_apr=0.11)
    assert conv.market is Market.CRYPTO
    assert conv.carry == pytest.approx(0.11)
    assert conv.model == "black76"


def test_equity_rates_do_not_leak_into_crypto():
    conv = for_symbol("binance-spot:BTCUSDT", 60.0, risk_free_rate=0.04,
                      dividend_yield=0.02, crypto_carry_apr=0.0)
    assert conv.carry == 0.0


def test_years_uses_the_same_clock_as_sigma():
    conv = for_symbol("SPY", 60.0)
    assert conv.years(conv.bars_per_year) == pytest.approx(1.0)


def test_constants_are_the_documented_ones():
    assert SECONDS_PER_CALENDAR_YEAR == 365.0 * 24.0 * 3600.0
    assert SECONDS_PER_EQUITY_SESSION == 6.5 * 3600.0
    assert EQUITY_SESSIONS_PER_YEAR == 252.0
