"""What separates a crypto symbol from an equity symbol.

Black-Scholes arithmetic is identical in both markets. Three *inputs* are not:
how a year is counted, what the carry is, and whether the model is written on
the spot (Black-Scholes-Merton) or on the forward (Black-76). All three live
here, so no other module in the Black-Scholes layer branches on market.

The discriminator is the symbol's shape — venue-qualified ("binance-spot:BTCUSDT")
is crypto, a bare ticker ("SPY") is an equity. `BotRunner.warmup` already treats
`":" in symbol` this way; reusing it avoids a second, drifting notion of market.
"""

from __future__ import annotations

import enum

import msgspec

#: Crypto trades continuously, so a year is a calendar year.
SECONDS_PER_CALENDAR_YEAR = 365.0 * 24.0 * 3600.0
#: US equity regular session, 09:30-16:00 ET.
SECONDS_PER_EQUITY_SESSION = 6.5 * 3600.0
#: Trading days in a US equity year.
EQUITY_SESSIONS_PER_YEAR = 252.0


class Market(enum.StrEnum):
    CRYPTO = "crypto"
    EQUITY = "equity"


def market_of(symbol: str) -> Market:
    """Crypto symbols are venue-qualified; equities are bare tickers."""
    return Market.CRYPTO if ":" in symbol else Market.EQUITY


def bars_per_year(market: Market, bar_s: float) -> float:
    """How many bars of ``bar_s`` seconds a year of THIS market contains.

    This single number carries the whole calendar difference. Volatility is
    annualized by its square root and horizons are divided by it, so both end up
    measured in the same clock — which is what stops an equity sigma annualized in
    trading time from being paired with a calendar-time horizon.
    """
    if bar_s <= 0.0:
        raise ValueError("bar_s must be positive")
    if market is Market.CRYPTO:
        return SECONDS_PER_CALENDAR_YEAR / bar_s
    return EQUITY_SESSIONS_PER_YEAR * (SECONDS_PER_EQUITY_SESSION / bar_s)


class MarketConvention(msgspec.Struct, frozen=True):
    """The calendar and carry for one symbol at one bar length."""

    market: Market
    bars_per_year: float
    #: Annualized continuous carry: r - q for equities, funding APR for crypto.
    carry: float

    @property
    def model(self) -> str:
        """Which of crocodile's two option models this market is written in."""
        return "black76" if self.market is Market.CRYPTO else "bsm"

    def years(self, bars: float) -> float:
        """Turn a bar count into years IN THIS MARKET'S CLOCK."""
        return bars / self.bars_per_year


def for_symbol(
    symbol: str,
    bar_s: float,
    *,
    risk_free_rate: float = 0.04,
    dividend_yield: float = 0.0,
    crypto_carry_apr: float = 0.0,
) -> MarketConvention:
    """Resolve the convention for ``symbol`` at a ``bar_s``-second bar.

    Carry is a configured constant in both markets. Live funding would need a
    crocodile ``Catalog`` (``funding_apr`` reads the stored ``funding`` channel)
    and the bot ingests none, so a constant is the honest default rather than a
    placeholder for data that is not there.
    """
    market = market_of(symbol)
    carry = crypto_carry_apr if market is Market.CRYPTO else risk_free_rate - dividend_yield
    return MarketConvention(
        market=market, bars_per_year=bars_per_year(market, bar_s), carry=carry
    )
