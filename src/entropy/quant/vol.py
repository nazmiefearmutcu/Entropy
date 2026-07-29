"""Where sigma comes from.

A ``VolSource`` answers one question — "what is this symbol's annualized
volatility right now?" — and is allowed to answer "I cannot". That last case is
why the protocol carries ``last_reason``: a source that quietly substitutes a
different estimator would make a run's ledger claim a model it did not use.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from .conventions import MarketConvention

__all__ = ["RealizedVolSource", "VolSource", "ewma_variance", "log_returns"]


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
    if any(not (math.isfinite(c) and c > 0.0) for c in closes):
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
        if len(closes) >= 2 and any(not (math.isfinite(c) and c > 0.0) for c in closes):
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
