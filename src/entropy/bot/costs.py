"""Per-market execution-cost model and the cost-aware financial math gates.

The paper executor and the strategies need to agree on what a trade costs,
otherwise the bot optimizes a fiction: the old flat ``fee_bps=1.0`` +
``slippage_bps=1.0`` understated round-trip costs by ~2x (equities) to ~6.5x
(crypto spot) versus the largest venues' real schedules (researched
2026-08-02; see the fee table below).

Fee schedules used for the defaults (per side, basis points, taker):

* Binance SPOT (regular tier): 10.0 bps (7.5 with the 25% BNB discount).
* Binance USDT-M FUTURES (regular tier): 5.0 bps (4.5 with the 10% BNB
  discount).
* US equities via Interactive Brokers tiered, ~$100k account, $5k-$30k
  notional: ~0.5-2.0 bps/side; the conservative 2.0 bps default absorbs the
  per-order minimums.

Slippage is a model input, not an exchange quote: 1-3 bps for the liquid
symbols this bot feeds on.

The derived math (all as fractions of notional unless stated otherwise):

* round-trip cost  ``C = 2*(fee_bps + slippage_bps)/10_000``
* breakeven move   ``m* = C``
* regime move floor: ``mean |per-bar return| >= k*C``
* expected-move gate: ``E|move| = sqrt(2/pi)*sigma_bar >= k*C``
* cost-to-stop ratio: ``C / stop_distance <= max_cost_to_stop``
* fee-adjusted Kelly (pure helper): ``f* = (b_net*p - q)/b_net`` with
  ``b_net = (W - C)/(L + C)``
"""

from __future__ import annotations

import enum
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from entropy.feeds.equities.universe import UNIVERSE

#: E|X| for X ~ N(0, sigma): sqrt(2/pi) * sigma — the expected per-bar move.
E_ABS_MOVE = math.sqrt(2.0 / math.pi)

#: Quote suffixes that mark a symbol as a crypto pair (``SOLUSDT`` etc.).
_CRYPTO_QUOTES = (
    "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI",
    "BTC", "ETH", "BNB", "XRP", "SOL", "DOGE", "ADA", "AVAX",
    "LINK", "DOT", "MATIC", "LTC", "BCH", "TRX", "SHIB", "EUR", "TRY",
)

#: Dated crypto futures carry a 6-digit expiry suffix (``BTCUSDT_250926``).
_DATED_FUTURES_RE = re.compile(r"_\d{6}$")


class MarketClass(enum.StrEnum):
    """The three cost regimes this bot can trade in."""

    EQUITY = "equity"
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_FUTURES = "crypto_futures"


def classify_symbol(symbol: str) -> MarketClass:
    """Map a symbol to its cost regime.

    Explicit venue prefixes win (``binance-futures:`` / ``futures:`` /
    ``binance-spot:`` / ``spot:`` / ``coinbase:`` / ``coinbase-spot:`` /
    ``crypto:``); otherwise a known equity universe member is an equity and a
    crypto-style suffix is spot (``SOLUSDT``, Coinbase's ``BTC-USD``). Perp and
    dated-futures suffixes (``-PERP``, ``-SWAP``, ``BTCUSDT_250926``) are
    futures. Unknown symbols fall back to the cheapest regime so the model
    never fabricates costs that block trading.
    """
    low = symbol.lower()
    if low.startswith(("binance-futures", "futures:")):
        return MarketClass.CRYPTO_FUTURES
    if low.startswith(
        ("binance-spot", "spot:", "coinbase:", "coinbase-spot:", "crypto:")
    ):
        return MarketClass.CRYPTO_SPOT
    if symbol in UNIVERSE:
        return MarketClass.EQUITY
    up = symbol.upper()
    if up.endswith(("-PERP", "-SWAP")) or _DATED_FUTURES_RE.search(up) is not None:
        return MarketClass.CRYPTO_FUTURES
    if up.endswith(("-USD", "-USDT", "-USDC")):
        return MarketClass.CRYPTO_SPOT
    if any(up.endswith(q) for q in _CRYPTO_QUOTES):
        return MarketClass.CRYPTO_SPOT
    return MarketClass.EQUITY


@dataclass(frozen=True, slots=True)
class MarketCosts:
    """One-way execution costs for one market, in basis points.

    ``None`` fields mean "inherit the flat fallback" until resolved by
    :meth:`CostModel.for_symbol`; callers outside the model always see
    concrete numbers.
    """

    fee_bps: float | None
    slippage_bps: float | None

    def resolved(
        self, flat_fee_bps: float, flat_slippage_bps: float
    ) -> MarketCosts:
        return MarketCosts(
            fee_bps=flat_fee_bps if self.fee_bps is None else self.fee_bps,
            slippage_bps=(
                flat_slippage_bps if self.slippage_bps is None else self.slippage_bps
            ),
        )

    @property
    def round_trip_bps(self) -> float:
        """Round-trip cost in bps: both sides pay fee + slippage."""
        fee = 0.0 if self.fee_bps is None else self.fee_bps
        slip = 0.0 if self.slippage_bps is None else self.slippage_bps
        return 2.0 * (fee + slip)

    @property
    def round_trip(self) -> float:
        """Round-trip cost as a fraction of notional."""
        return self.round_trip_bps / 10_000.0

    @property
    def breakeven_move(self) -> float:
        """Minimum price move (fraction) that covers the round trip."""
        return self.round_trip

    def minimum_move(self, edge_mult: float = 2.0) -> float:
        """Regime floor ``k*C``: a bar must move this much to be tradeable."""
        return edge_mult * self.round_trip

    def sigma_gate(self, edge_mult: float = 2.0) -> float:
        """Per-bar volatility floor implied by ``E|move| >= k*C``."""
        return edge_mult * self.round_trip / E_ABS_MOVE

    def cost_to_stop(self, stop_pct: float) -> float:
        """``C / stop_distance`` with ``stop_pct`` in percent — churn red flag.

        A 1% stop is 100 bps, so the ratio is ``round_trip_bps / (stop_pct*100)``.
        The churn bound is applied by the caller via ``max_cost_to_stop``, not
        pinned here.
        """
        if stop_pct <= 0.0:
            return math.inf
        return self.round_trip_bps / (stop_pct * 100.0)


class CostModel:
    """Resolves per-symbol costs: market overrides, else the flat fallback.

    ``market`` maps :class:`MarketClass` to explicit costs; a class missing
    from the mapping falls back to ``flat_fee_bps``/``flat_slippage_bps``.
    """

    def __init__(
        self,
        flat_fee_bps: float = 1.0,
        flat_slippage_bps: float = 1.0,
        market: Mapping[MarketClass, MarketCosts] | None = None,
    ) -> None:
        self.flat_fee_bps = flat_fee_bps
        self.flat_slippage_bps = flat_slippage_bps
        self.market = dict(market) if market else {}

    def for_symbol(self, symbol: str) -> MarketCosts:
        cls = classify_symbol(symbol)
        costs = self.market.get(cls)
        if costs is not None:
            return costs.resolved(self.flat_fee_bps, self.flat_slippage_bps)
        return MarketCosts(
            fee_bps=self.flat_fee_bps, slippage_bps=self.flat_slippage_bps
        )

    def round_trip_bps(self, symbol: str) -> float:
        return self.for_symbol(symbol).round_trip_bps

    def breakeven(self, symbol: str) -> float:
        """Round-trip cost as a fraction of notional for ``symbol``."""
        return self.for_symbol(symbol).breakeven_move

    def minimum_move(self, symbol: str, edge_mult: float = 2.0) -> float:
        return self.for_symbol(symbol).minimum_move(edge_mult)

    def sigma_gate(self, symbol: str, edge_mult: float = 2.0) -> float:
        return self.for_symbol(symbol).sigma_gate(edge_mult)

    def cost_to_stop(self, symbol: str, stop_pct: float) -> float:
        return self.for_symbol(symbol).cost_to_stop(stop_pct)


def fee_adjusted_kelly(
    win_rate: float,
    payoff_ratio: float,
    round_trip: float,
) -> float:
    """Cost-adjusted Kelly fraction ``f* = (b_net*p - q)/b_net``.

    ``b_net = (W - C)/(L + C)`` assumes ``W = payoff_ratio*L`` and charges the
    round-trip cost ``C`` (fraction of notional) on every trade. A zero or
    negative net edge clamps to 0.0 — never a negative fraction. A bot should
    risk a fraction of this (e.g. 0.1-0.25x), never full Kelly.
    """
    if not 0.0 < win_rate < 1.0 or payoff_ratio <= 0.0 or round_trip < 0.0:
        raise ValueError("win_rate in (0,1), payoff_ratio > 0, round_trip >= 0 required")
    loss = 1.0
    win = payoff_ratio
    b_net = (win - round_trip) / (loss + round_trip)
    if b_net <= 0.0:
        return 0.0
    edge = b_net * win_rate - (1.0 - win_rate)
    if edge <= 0.0:
        return 0.0
    return edge / b_net
