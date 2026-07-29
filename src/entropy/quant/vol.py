"""Where sigma comes from.

A ``VolSource`` answers one question — "what is this symbol's annualized
volatility right now?" — and is allowed to answer "I cannot". That last case is
why the protocol carries ``last_reason``: a source that quietly substitutes a
different estimator would make a run's ledger claim a model it did not use.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .conventions import Market, MarketConvention

__all__ = [
    "ChainVolSource",
    "RealizedVolSource",
    "VolSource",
    "ewma_variance",
    "log_returns",
]


def _is_usable_close(c: float) -> bool:
    """A price this module is willing to compute a return from.

    One spelling, deliberately. This predicate was written out twice — in
    ``log_returns`` and again in ``RealizedVolSource.sigma`` — for two callers
    that MUST agree: if the guard drifts, one of them accepts a price the other
    voids and the disagreement shows up as a sigma computed from a shorter
    series rather than as an error. A safety predicate gets one definition.
    """
    return math.isfinite(c) and c > 0.0


def log_returns(closes: Sequence[float]) -> list[float]:
    """Per-bar log returns. Any non-positive OR non-finite price voids the series.

    A zero, negative, ``nan`` or ``inf`` close means the tape lied, not that the
    return was large, and a silent skip would splice two disjoint stretches of
    price into one return.

    ``nan`` is the dangerous one: it fails every ordering comparison, so a
    ``c <= 0.0`` guard waves it through and it propagates to an all-``nan``
    sigma. ``inf`` is merely loud — ``100.0 / inf`` is ``0.0`` and ``log`` then
    raises. Both are refused here rather than returned as a number.
    """
    if len(closes) < 2:
        return []
    if any(not _is_usable_close(c) for c in closes):
        return []
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]


def ewma_variance(returns: Sequence[float], lam: float) -> float:
    """RiskMetrics EWMA: var_t = lam*var_(t-1) + (1-lam)*r_t^2.

    Seeded with the sample second moment so the first few steps are not dominated
    by whichever return happened to arrive first.
    """
    n = len(returns)
    if n == 0:
        return 0.0
    var = sum(r * r for r in returns) / n
    for r in returns:
        var = lam * var + (1.0 - lam) * r * r
    return var


class VolSource(Protocol):
    """Annualized sigma for a symbol, or ``None`` with a reason."""

    name: str
    #: Why the last call returned ``None``; empty when it returned a number.
    last_reason: str

    def sigma(
        self, symbol: str, closes: Sequence[float], conv: MarketConvention
    ) -> float | None: ...


class RealizedVolSource:
    """EWMA of squared log returns, annualized in the convention's clock.

    Deterministic and I/O-free, which is what makes it honest against BOTH of
    this bot's tapes — including the equity simulator, where any externally
    sourced volatility would describe a market these prices did not come from.
    """

    name = "realized"

    def __init__(
        self, *, lam: float = 0.94, floor: float = 0.05, min_returns: int = 10
    ) -> None:
        if not 0.0 < lam < 1.0:
            raise ValueError(f"lam must be in (0, 1), got {lam!r}")
        if floor <= 0.0:
            raise ValueError(f"floor must be positive, got {floor!r}")
        if min_returns < 1:
            raise ValueError(f"min_returns must be >= 1, got {min_returns!r}")
        self.lam = lam
        self.floor = floor
        self.min_returns = min_returns
        self.last_reason = ""

    def sigma(
        self, symbol: str, closes: Sequence[float], conv: MarketConvention
    ) -> float | None:
        # A corrupt print gets its own reason. Otherwise one bad bar in a 500-bar
        # window empties `returns` and reports a history-length problem, pointing
        # the operator at the warmup budget instead of at the tape.
        if len(closes) >= 2 and any(not _is_usable_close(c) for c in closes):
            self.last_reason = "non-finite or non-positive close in the window"
            return None
        returns = log_returns(closes)
        if len(returns) < self.min_returns:
            self.last_reason = (
                f"need {self.min_returns} usable returns, have {len(returns)}"
            )
            return None
        variance = ewma_variance(returns, self.lam)
        self.last_reason = ""
        return max(math.sqrt(max(variance, 0.0) * conv.bars_per_year), self.floor)


class ChainVolSource:
    """ATM implied volatility from a live option chain, via crocodile's surface.

    Gated on purpose, and it refuses in five distinct cases:

    * **Equities.** This bot's equity tape is ``EquitySimFeed``. Splicing real
      option-implied volatility onto synthetic ~$100 ticks describes a market
      those prices did not come from — the same mismatch ``BotRunner.warmup``
      already refuses for real bars.
    * **No catalog.** ``term_structure`` reads an ``options_chain`` channel out of
      a crocodile ``Catalog`` (duckdb) and the bot ingests none, so without one
      configured there is nothing to read.
    * **No usable chain.** An empty frame, or one with no ``atm_iv`` column.
    * **Every expiry already passed.** Expired rows are present, not filtered
      out, so "nearest" has to mean nearest AHEAD.
    * **Unusable ATM vol.** Null, non-finite or non-positive. ``nan`` is included
      because it is the dangerous one: it fails every ordering comparison, so a
      bare ``<= 0.0`` test would wave it through to a maximum-conviction entry.

    In every case it returns ``None`` and sets ``last_reason``. It never falls
    back to realized volatility: a run configured for implied vol that quietly
    traded on realized vol would leave a ledger naming a model it did not use.
    """

    name = "chain"

    def __init__(
        self,
        catalog: object | None = None,
        *,
        now_ns: Callable[[], int] | None = None,
    ) -> None:
        self.catalog = catalog
        self._now_ns = now_ns
        self.last_reason = ""

    def _term_structure(self, catalog: object, underlying: str, at_ns: int) -> Any:
        """Seam for tests; the real call goes to crocodile.

        ``model`` is required, not defaulted, and Black-76 is not a guess here:
        ``sigma`` refuses equities before this is reached, so crypto is the only
        market that gets this far, and ``black76`` is the model
        ``MarketConvention.model`` names for it.
        """
        from crocodile.core.analytics.volsurface import term_structure
        from crocodile.crypto.analytics.volsurface import BLACK76

        return term_structure(
            catalog,  # type: ignore[arg-type]
            underlying,
            at_ns,
            model=BLACK76,
        )

    def sigma(
        self, symbol: str, closes: Sequence[float], conv: MarketConvention
    ) -> float | None:
        if conv.market is Market.EQUITY:
            self.last_reason = (
                "equity tape is the simulator; real implied vol would describe "
                "a different market"
            )
            return None
        if self.catalog is None:
            self.last_reason = "no options catalog configured"
            return None
        # Lazy, like the crocodile import in `_term_structure`: importing this
        # module must stay free, and `entropy.quant`'s default path pure.
        import polars as pl

        now = self._now_ns() if self._now_ns is not None else time.time_ns()
        # Everything that touches the frame lives inside the `try`. A class whose
        # whole contract is "return None and say why" must not have a path that
        # raises instead — a frame missing `days_to_expiry`, or a seam handing back
        # something that is not a DataFrame at all, becomes a reason like any
        # other failure rather than an exception escaping into the strategy.
        try:
            frame = self._term_structure(self.catalog, symbol, now)
            if frame is None or frame.is_empty() or "atm_iv" not in frame.columns:
                self.last_reason = "no chain rows for this underlying"
                return None
            # Expired expiries are in the frame, not filtered out of it. Crocodile
            # solves an IV per expiry it can see; `_black76_iv` requires
            # `t_years > 0.0` and returns `None` for one that has passed, and
            # `iv_surface` appends that row regardless. It arrives with a NEGATIVE
            # `days_to_expiry` and a null `atm_iv` — so an unfiltered sort takes the
            # MOST expired contract, and the source refuses permanently with a live
            # quote sitting one row below. "Nearest" has to mean nearest ahead.
            live = frame.filter(pl.col("days_to_expiry") > 0.0)
            if live.is_empty():
                self.last_reason = "every expiry in the chain has already passed"
                return None
            iv = live.sort("days_to_expiry").row(0, named=True).get("atm_iv")
            # `nan` fails every ordering comparison, so a bare `<= 0.0` waves it
            # through. Downstream it does not merely propagate: `divergence_score`
            # ends in `max(-1.0, min(1.0, x))` and `min(1.0, nan)` is `1.0`, so a
            # `nan` implied vol clamps to a maximum-conviction entry — the exact
            # inverse of this class's contract. `RealizedVolSource` above already
            # refuses non-finite values for the same reason.
            if iv is None or not math.isfinite(float(iv)) or float(iv) <= 0.0:
                self.last_reason = f"unusable atm iv {iv!r} at the nearest expiry"
                return None
        except Exception as exc:  # surfaced, never swallowed
            self.last_reason = f"term structure failed: {exc}"
            return None
        self.last_reason = ""
        return float(iv)
