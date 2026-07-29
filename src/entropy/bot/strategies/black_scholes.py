"""A directional spot strategy built on the Black-Scholes distribution.

The bot does not trade options, so this does not price any. What it takes from
Black-Scholes is the law the model assumes for the underlying: log-normal
returns with an annualized volatility and a drift, under which the probability
of finishing beyond a strike is N(d2). Substituting an estimated drift for the
carry and reading how far the barrier probabilities move is the whole signal.

Score, in one sentence: how much more likely than a carry-neutral world does the
tape make a one-sigma up move over the horizon, net of how much more likely it
makes a one-sigma down move.

Everything that differs between crypto and equities is in
:class:`~entropy.quant.conventions.MarketConvention` — the calendar the
volatility is annualized in, the carry, and whether the model is written on the
spot or the forward. This file selects one per symbol and does no market
branching of its own.

Honest limitation, stated here because it also belongs in any reading of the
output: the drift estimate is the weak input. Measured over tens of bars its
standard error swamps it. Shrinkage toward the carry and a cap of
``drift_cap_sigmas`` bound the damage but do not remove it, so the score is best
read as a normalized, carry-adjusted momentum reading — Black-Scholes supplies
the correct normalization and the correct carry, not a forecast. Like
``ConsensusStrategy``, this targets signal quality, not demonstrated edge.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from entropy.engine.events import Event
from entropy.quant.conventions import MarketConvention, for_symbol
from entropy.quant.distribution import divergence_score, risk_hints, shrink_drift
from entropy.quant.vol import RealizedVolSource, VolSource, log_returns
from entropy.strategy.engine import Bar

from ..signals import Signal, SignalAction

_NS_PER_S = 1_000_000_000
_CLOSES_MAXLEN = 512

#: What ends a position.
EXIT_MODES = ("score", "flip", "either")


@dataclass(frozen=True, slots=True)
class _Label:
    """Regime-style label for the dashboards, which read ``.label``."""

    label: str


@dataclass(slots=True)
class _SymbolState:
    closes: deque[float] = field(default_factory=lambda: deque(maxlen=_CLOSES_MAXLEN))
    bucket: int | None = None
    bar_close: float = 0.0
    pending: bool = False
    direction: int = 0
    bars_in_trade: int = 0
    bars_since_exit: int = 1 << 30


class BlackScholesStrategy:
    name = "black_scholes"

    def __init__(
        self,
        symbols: tuple[str, ...] | None = None,
        bar_s: float = 60.0,
        *,
        horizon_bars: int = 30,
        barrier_k: float = 1.0,
        threshold: float = 0.15,
        min_bars: int = 40,
        drift_window: int = 20,
        drift_shrinkage: float = 0.5,
        drift_cap_sigmas: float = 3.0,
        z_stop: float = 1.0,
        z_tp: float = 2.0,
        stop_floor_pct: float = 0.05,
        risk_budget_pct: float = 1.0,
        max_size_pct: float = 100.0,
        risk_free_rate: float = 0.04,
        dividend_yield: float = 0.0,
        crypto_carry_apr: float = 0.0,
        min_hold_bars: int = 3,
        cooldown_bars: int = 2,
        exit_mode: str = "score",
        vol_source: VolSource | None = None,
        warmup_symbol: str | None = None,
    ) -> None:
        if bar_s <= 0.0:
            raise ValueError("bar_s must be positive")
        if horizon_bars < 1:
            raise ValueError("horizon_bars must be >= 1")
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        if barrier_k <= 0.0:
            raise ValueError("barrier_k must be positive")
        if drift_window < 2:
            raise ValueError("drift_window must be >= 2")
        if not 0.0 <= drift_shrinkage <= 1.0:
            raise ValueError("drift_shrinkage must be in [0, 1]")
        if drift_cap_sigmas <= 0.0:
            raise ValueError("drift_cap_sigmas must be positive")
        if exit_mode not in EXIT_MODES:
            raise ValueError(f"exit_mode must be one of {EXIT_MODES}")

        self.symbols = symbols  # None = trade every symbol
        self.bar_s = bar_s
        self.horizon_bars = horizon_bars
        self.barrier_k = barrier_k
        self.threshold = threshold
        self.drift_window = drift_window
        self.drift_shrinkage = drift_shrinkage
        self.drift_cap_sigmas = drift_cap_sigmas
        self.z_stop, self.z_tp = z_stop, z_tp
        self.stop_floor_pct = stop_floor_pct
        self.risk_budget_pct = risk_budget_pct
        self.max_size_pct = max_size_pct
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield
        self.crypto_carry_apr = crypto_carry_apr
        self.min_hold_bars = max(0, min_hold_bars)
        self.cooldown_bars = max(0, cooldown_bars)
        self.exit_mode = exit_mode
        self.vol_source: VolSource = vol_source or RealizedVolSource()
        self.warmup_symbol = warmup_symbol
        # The drift estimate needs its own window plus one close to difference it.
        self.min_bars = max(min_bars, drift_window + 1)
        self._bar_ns = max(1, int(bar_s * _NS_PER_S))
        self._states: dict[str, _SymbolState] = {}
        self._conventions: dict[str, MarketConvention] = {}
        self._seed: _SymbolState | None = None
        #: Dashboards read ``.label`` off these, same shape as ConsensusStrategy.
        self.last_regime: dict[str, _Label] = {}
        self.last_sigma: dict[str, float] = {}
        self.last_score: dict[str, float] = {}
        #: Why a symbol produced no signal on its last evaluated bar.
        self.last_rejects: dict[str, str] = {}

    # ---- market resolution ----------------------------------------------

    def convention_for(self, symbol: str) -> MarketConvention:
        """The calendar and carry for ``symbol``, resolved once and cached."""
        conv = self._conventions.get(symbol)
        if conv is None:
            conv = for_symbol(
                symbol,
                self.bar_s,
                risk_free_rate=self.risk_free_rate,
                dividend_yield=self.dividend_yield,
                crypto_carry_apr=self.crypto_carry_apr,
            )
            self._conventions[symbol] = conv
        return conv

    # ---- warmup ---------------------------------------------------------

    def warmup(self, bars: Sequence[Bar]) -> None:
        """Seed bar closes so live ticks are immediately eligible for signals.

        ``Bar`` carries no symbol, so the seed goes to the explicit ``symbols``
        (or ``warmup_symbol``) when there is one, and otherwise to whichever
        symbol ticks first — the same best-effort contract ConsensusStrategy has.
        """
        if not bars:
            return
        proto = _SymbolState()
        for b in bars:
            proto.closes.append(float(b.close))
        # The last warmup bar is already committed: mark its bucket current and
        # not pending, so the first live bucket roll does not double-append it.
        proto.bucket = bars[-1].ts_ns // self._bar_ns
        proto.bar_close = float(bars[-1].close)
        proto.pending = False
        targets = self.symbols or ((self.warmup_symbol,) if self.warmup_symbol else ())
        if targets:
            for sym in targets:
                self._states[sym] = _SymbolState(
                    closes=deque(proto.closes, maxlen=_CLOSES_MAXLEN),
                    bucket=proto.bucket,
                    bar_close=proto.bar_close,
                    pending=False,
                )
        else:
            self._seed = proto

    # ---- position lifecycle ---------------------------------------------

    def on_position_closed(self, symbol: str, reason: str) -> None:
        """Forget a position that ended without this strategy asking.

        A mechanical stop or take-profit leaves ``direction`` pointing at a trade
        the portfolio does not hold; left unreported the strategy would wait for
        its own exit condition and stay mute for the rest of the move.
        """
        st = self._states.get(symbol)
        if st is None or st.direction == 0:
            return
        st.direction = 0
        st.bars_in_trade = 0
        st.bars_since_exit = 0

    # ---- hot path -------------------------------------------------------

    def on_tick(
        self, symbol: str, price: float, ts_ns: int, events: Sequence[Event]
    ) -> list[Signal]:
        if self.symbols is not None and symbol not in self.symbols:
            return []
        st = self._states.get(symbol)
        if st is None:
            if self._seed is not None:
                st, self._seed = self._seed, None
            else:
                st = _SymbolState()
            self._states[symbol] = st
        bucket = ts_ns // self._bar_ns
        if st.bucket == bucket:
            st.bar_close = price
            st.pending = True
            return []
        committed = st.bucket is not None and st.pending
        if committed:
            st.closes.append(st.bar_close)
            # Counters advance on COMPLETED bars, so min_hold and cooldown are
            # measured in bars however dense the tick stream is.
            if st.direction != 0:
                st.bars_in_trade += 1
            elif st.bars_since_exit < (1 << 30):
                st.bars_since_exit += 1
        st.bucket = bucket
        st.bar_close = price
        st.pending = True
        if not committed:
            return []
        return self._evaluate(symbol, ts_ns, st)

    # ---- evaluation (completed bars only) --------------------------------

    def _evaluate(self, symbol: str, ts_ns: int, st: _SymbolState) -> list[Signal]:
        closes = list(st.closes)
        if len(closes) < self.min_bars:
            return []
        conv = self.convention_for(symbol)

        sigma = self.vol_source.sigma(symbol, closes, conv)
        if sigma is None or sigma <= 0.0:
            # No silent substitution: say which source refused and why, then stay
            # out. A quietly downgraded model would make the ledger attribute
            # these trades to a model the run did not use.
            self.last_rejects[symbol] = (
                f"{self.vol_source.name}: {self.vol_source.last_reason or 'no sigma'}"
            )
            return []
        self.last_rejects.pop(symbol, None)

        t_years = conv.years(self.horizon_bars)
        raw_drift = self._raw_drift(closes, conv)
        drift = shrink_drift(
            raw_drift, conv.carry,
            shrinkage=self.drift_shrinkage, sigma=sigma, t_years=t_years,
            cap_sigmas=self.drift_cap_sigmas,
        )
        spot = closes[-1]
        score = divergence_score(
            spot, sigma, t_years,
            carry=conv.carry, drift=drift, barrier_k=self.barrier_k,
        )
        self.last_sigma[symbol] = sigma
        self.last_score[symbol] = score
        self.last_regime[symbol] = _Label(f"{conv.market.value} σ{sigma * 100:.0f}%")
        reason = (
            f"bs {score:+.2f} [{conv.market.value} σ{sigma * 100:.0f}% "
            f"T{self.horizon_bars}b] (μ̂ {drift:+.3f} c {conv.carry:+.3f})"
        )

        if st.direction == 0:
            if st.bars_since_exit < self.cooldown_bars:
                return []
            if abs(score) < self.threshold:
                return []
            st.direction = 1 if score > 0 else -1
            st.bars_in_trade = 0
            hints = risk_hints(
                sigma, t_years,
                z_stop=self.z_stop, z_tp=self.z_tp,
                stop_floor_pct=self.stop_floor_pct,
                risk_budget_pct=self.risk_budget_pct,
                max_size_pct=self.max_size_pct,
            )
            action = (
                SignalAction.ENTER_LONG if score > 0 else SignalAction.ENTER_SHORT
            )
            return [Signal(
                symbol=symbol, action=action, strength=abs(score), reason=reason,
                ts_ns=ts_ns, strategy=self.name,
                size_pct=hints.size_pct, stop_pct=hints.stop_pct, tp_pct=hints.tp_pct,
            )]

        if st.bars_in_trade < self.min_hold_bars:
            return []
        if not self._should_exit(score, st.direction):
            return []
        st.direction = 0
        st.bars_in_trade = 0
        st.bars_since_exit = 0
        # Exits carry no hints: closing happens at the mark, and a stop distance
        # on a close would be meaningless.
        return [Signal(symbol=symbol, action=SignalAction.EXIT, strength=1.0,
                       reason=reason, ts_ns=ts_ns, strategy=self.name)]

    def _raw_drift(self, closes: list[float], conv: MarketConvention) -> float:
        """Annualized mean log return over the drift window."""
        window = closes[-(self.drift_window + 1):]
        returns = log_returns(window)
        if not returns:
            return conv.carry
        return sum(returns) / len(returns) * conv.bars_per_year

    def _should_exit(self, score: float, direction: int) -> bool:
        """Hysteresis: the score must fall back through ``threshold / 2`` against
        the tracked direction, and/or cross zero — per ``exit_mode``."""
        band = self.threshold / 2.0
        by_score = (direction > 0 and score < band) or (direction < 0 and score > -band)
        by_flip = (direction > 0 and score < 0.0) or (direction < 0 and score > 0.0)
        if self.exit_mode == "score":
            return by_score
        if self.exit_mode == "flip":
            return by_flip
        return by_score or by_flip
