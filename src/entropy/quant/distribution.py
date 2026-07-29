"""From a distribution to a decision.

Two translations live here and nothing else. The first turns a pair of drifts
into a score: hold sigma and the horizon fixed, move only the drift, and read
how far the barrier-crossing probabilities shift. The second turns sigma and the
horizon into a stop distance and the position size that spends a fixed risk
budget at that stop.

Both are pure functions of numbers. The strategy owns the state machine, the
risk manager owns the guards, and neither of them does arithmetic.
"""

from __future__ import annotations

import math

import msgspec

from .pricing import prob_above, prob_below

#: Mirrors ``entropy.bot.risk.manager._MAX_STOP_TP_PCT`` (50.0) as a fraction.
#: Duplicated deliberately: this module must not import the bot, and a hint that
#: exceeded the risk manager's ceiling would be silently truncated there anyway.
MAX_STOP_TP_FRAC = 0.50

#: Practical supremum of ``divergence_score``'s magnitude — see its docstring.
#: The score's own clamp is 1.0, but the limit as the drift runs to infinity is
#: ``(1 - N(-k+y) + N(-k-y)) / 2``, which is 1/2 at ``carry == sigma**2/2`` and
#: measured 0.481-0.511 over ordinary parameters. A ``threshold`` above this can
#: never be crossed, so the bot's config validator refuses one rather than
#: shipping a strategy that is silently incapable of entering.
MAX_REACHABLE_SCORE = 0.5

__all__ = [
    "MAX_REACHABLE_SCORE",
    "MAX_STOP_TP_FRAC",
    "RiskHints",
    "barrier",
    "divergence_score",
    "risk_hints",
    "shrink_drift",
]


class RiskHints(msgspec.Struct, frozen=True):
    """What a strategy proposes to the risk manager. Percentages, not fractions."""

    size_pct: float
    stop_pct: float
    tp_pct: float


def barrier(spot: float, sigma: float, t_years: float, k: float, *, up: bool) -> float:
    """The strike ``k`` standard deviations away over the horizon.

    Scaling by ``sigma*sqrt(T)`` rather than by a fixed percentage is what makes
    one setting work on a $4 stock and a $70k coin: the barrier is always the
    same distance in the units the market actually moves in.
    """
    move = k * sigma * math.sqrt(t_years)
    return spot * math.exp(move if up else -move)


def shrink_drift(
    raw: float,
    carry: float,
    *,
    shrinkage: float,
    sigma: float,
    t_years: float,
    cap_sigmas: float,
) -> float:
    """Pull an estimated drift toward the carry and bound how far it may stray.

    Drift measured over tens of bars has a standard error that swamps the
    estimate — that is a property of the data, not of this code. Shrinkage
    toward the carry and a cap of ``cap_sigmas`` standard deviations over the
    horizon bound the damage; they do not remove it.

    The cap is stated on ``|mu - carry| * T <= cap_sigmas * sigma * sqrt(T)``,
    i.e. "the drift may not move the mean more than N standard deviations", which
    rearranges to the annualized limit below.
    """
    mu = shrinkage * raw + (1.0 - shrinkage) * carry
    if t_years <= 0.0 or sigma <= 0.0:
        return carry
    limit = cap_sigmas * sigma / math.sqrt(t_years)
    delta = mu - carry
    if delta > limit:
        return carry + limit
    if delta < -limit:
        return carry - limit
    return mu


def divergence_score(
    spot: float,
    sigma: float,
    t_years: float,
    *,
    carry: float,
    drift: float,
    barrier_k: float,
) -> float:
    """How far the tape's drift moves the barrier probabilities off carry-neutral.

    Returns a number in [-1, 1]. Positive means an up-move is more likely than a
    carry-neutral world would say, net of the down side; zero means the tape adds
    nothing the carry did not already imply.

    That [-1, 1] clamp is defensive and never binds. As ``drift`` runs to +infinity
    the up leg gains at most ``1 - N(-k+y)`` while the down leg gives up ``N(-k-y)``,
    where ``y = (carry - sigma**2/2) * sqrt(T) / sigma``, so the score tends to
    ``(1 - N(-k+y) + N(-k-y)) / 2`` — exactly 1/2 when ``carry == sigma**2/2``, which
    is where symmetric barriers make the two carry-neutral probabilities cancel. Away
    from that carry the bound is only near a half (measured 0.481 to 0.511 over
    ``barrier_k`` in [0.5, 2] and carry in [0, 0.5] at sigma=0.6, T=0.01); it climbs
    toward 1 only as ``y`` runs far negative (0.998 at ``y = -3.6``) and reaches it
    nowhere. So read a threshold against ~0.5 of reachable scale, not 1.0: a
    ``threshold`` of 0.15 is roughly 30% of what this score can actually reach, not 15%.
    """
    k_up = barrier(spot, sigma, t_years, barrier_k, up=True)
    k_dn = barrier(spot, sigma, t_years, barrier_k, up=False)
    edge_up = prob_above(spot, k_up, t_years, sigma, drift) - prob_above(
        spot, k_up, t_years, sigma, carry
    )
    edge_dn = prob_below(spot, k_dn, t_years, sigma, drift) - prob_below(
        spot, k_dn, t_years, sigma, carry
    )
    return max(-1.0, min(1.0, (edge_up - edge_dn) / 2.0))


def risk_hints(
    sigma: float,
    t_years: float,
    *,
    z_stop: float,
    z_tp: float,
    stop_floor_pct: float,
    risk_budget_pct: float,
    max_size_pct: float,
) -> RiskHints:
    """Stop, target and size from one sigma and one horizon.

    Size follows from "lose ``risk_budget_pct`` of equity if the stop is hit":
    notional N at stop distance ``stop_frac`` loses ``N * stop_frac``, so
    ``size_pct = risk_budget_pct / stop_frac``. It uses the CLAMPED stop, so the
    budget stays true to the stop that is actually placed.

    ``max_size_pct`` is the profile's per-trade allowance and is a ceiling only —
    this function can ask for less risk, never more.

    Read the budget as a CEILING, not as the governing term. The ``min`` picks it
    only when ``stop_frac > risk_budget_pct / max_size_pct``; at the bot's
    shipped numbers (1.0% budget, MEDIUM's 2.5% per trade) that crossover is a
    stop wider than 40%, which ``sigma * sqrt(T)`` does not reach at short
    horizons — 30 one-minute crypto bars at sigma=0.60 give a 0.4533% stop, so
    ``max_size_pct`` wins and ``size_pct`` is *exactly* the profile's per-trade
    percentage on every ordinary entry. Actual risk-at-stop is then
    ``2.5% * 0.4533% = 0.0113%`` of equity against a stated 1% budget, 88x
    smaller (117x at sigma=0.45). The budget starts governing only on genuinely
    wide stops — sigma above ~52.9 annualized on that horizon, or a much longer
    ``t_years``. This is the safe direction of the two, but it means raising
    ``risk_budget_pct`` moves the crossover rather than the size.
    """
    root = sigma * math.sqrt(t_years)
    floor = stop_floor_pct / 100.0
    stop_frac = min(max(z_stop * root, floor), MAX_STOP_TP_FRAC)
    tp_frac = min(max(z_tp * root, floor), MAX_STOP_TP_FRAC)
    return RiskHints(
        size_pct=min(max_size_pct, risk_budget_pct / stop_frac),
        stop_pct=100.0 * stop_frac,
        tp_pct=100.0 * tp_frac,
    )
