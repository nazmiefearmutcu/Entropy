"""Multi-indicator consensus strategy with a regime filter.

Ticks are aggregated into fixed-length bars; on every *completed* bar the
strategy combines four indicator votes (EMA cross, MACD histogram, RSI,
Bollinger %B) into a weighted score in [-1, 1]. Entries additionally require
a tradeable regime (enough realized volatility and a non-flat EMA slope) and
exits use a half-threshold hysteresis band so a single noisy bar does not
churn the position. This targets better *signal quality* than the
single-indicator strategies; it is not a claim of live trading edge.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from entropy.engine.events import Event
from entropy.strategy.engine import Bar

from ..signals import Signal, SignalAction

_NS_PER_S = 1_000_000_000
_CLOSES_MAXLEN = 128

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "ema": 0.35,
    "macd": 0.30,
    "rsi": 0.20,
    "bollinger": 0.15,
}

_VOTE_CHAR = {1: "+", 0: "0", -1: "-"}


@dataclass(frozen=True, slots=True)
class Votes:
    """One -1/0/+1 vote per indicator for a completed bar."""

    ema: int
    macd: int
    rsi: int
    bollinger: int


def score_votes(votes: Votes, weights: Mapping[str, float]) -> float:
    """Weighted consensus score, normalized into [-1, 1] by the total weight."""
    total = sum(abs(w) for w in weights.values())
    if total <= 0.0:
        return 0.0
    raw = (
        weights.get("ema", 0.0) * votes.ema
        + weights.get("macd", 0.0) * votes.macd
        + weights.get("rsi", 0.0) * votes.rsi
        + weights.get("bollinger", 0.0) * votes.bollinger
    )
    return raw / total


@dataclass(slots=True)
class _SymbolState:
    closes: deque[float] = field(default_factory=lambda: deque(maxlen=_CLOSES_MAXLEN))
    bucket: int | None = None  # current bar's time bucket (ts_ns // bar_ns)
    bar_close: float = 0.0  # latest price seen inside the current bucket
    pending: bool = False  # True once a live tick landed in the current bucket
    direction: int = 0  # what THIS strategy last signaled: +1 long, -1 short, 0 flat


class ConsensusStrategy:
    """Weighted multi-indicator consensus with hysteresis and a regime filter.

    Votes (per completed bar, needs >= ``min_bars`` closes):

    * EMA(9) vs EMA(21): fast above -> +1, below -> -1
    * MACD(12, 26, 9) histogram sign
    * RSI(14): < 30 -> +1 (mean-revert long), > 70 -> -1
    * Bollinger(20, 2.0) %B: < 0.05 -> +1, > 0.95 -> -1

    Entries require ``|score| >= threshold`` AND the regime filter to pass;
    exits fire when the score falls back through ``threshold / 2`` against the
    tracked direction (regime-exempt). The strategy only tracks what it has
    signaled itself — actual position state lives in the portfolio/risk layer.
    """

    name = "consensus"

    def __init__(
        self,
        symbols: tuple[str, ...] | None = None,
        bar_s: float = 5.0,
        threshold: float = 0.5,
        min_bars: int = 35,
        weights: dict[str, float] | None = None,
        vol_floor: float = 0.0005,
        slope_min: float = 0.0002,
    ) -> None:
        if bar_s <= 0.0:
            raise ValueError("bar_s must be positive")
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        self.symbols = symbols  # None = trade every symbol
        self.bar_s = bar_s
        self.threshold = threshold
        self.min_bars = min_bars
        self.weights: Mapping[str, float] = dict(weights) if weights else dict(DEFAULT_WEIGHTS)
        self.vol_floor = vol_floor
        self.slope_min = slope_min
        self._bar_ns = max(1, int(bar_s * _NS_PER_S))
        self._states: dict[str, _SymbolState] = {}
        # Warmup seed for symbols==None: `Bar` carries no symbol, so seeded
        # closes are adopted by the first symbol that ticks (best effort).
        self._seed: _SymbolState | None = None

    # ---- warmup ---------------------------------------------------------

    def warmup(self, bars: Sequence[Bar]) -> None:
        """Seed bar closes so live ticks are immediately eligible for signals."""
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
        if self.symbols:
            for sym in self.symbols:
                self._states[sym] = _SymbolState(
                    closes=deque(proto.closes, maxlen=_CLOSES_MAXLEN),
                    bucket=proto.bucket,
                    bar_close=proto.bar_close,
                    pending=False,
                )
        else:
            self._seed = proto

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
            st.bar_close = price  # still inside the current bar: track its close
            st.pending = True
            return []
        committed = st.bucket is not None and st.pending
        if committed:
            st.closes.append(st.bar_close)
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
        # Lazy import: stockodile.analytics pulls numpy+polars (~0.3 s); pay it
        # on the first evaluated bar, not at bot startup. Cached in sys.modules.
        from stockodile.analytics import (
            calculate_bollinger_bands,
            calculate_ema,
            calculate_macd,
            calculate_rsi,
        )

        ema_fast = calculate_ema(closes, 9)
        ema_slow = calculate_ema(closes, 21)
        _, _, macd_hist = calculate_macd(closes, 12, 26, 9)
        rsi = calculate_rsi(closes, 14)
        bb_upper, _, bb_lower = calculate_bollinger_bands(closes, 20, 2.0)

        close = closes[-1]
        votes = Votes(
            ema=_cmp_vote(ema_fast[-1], ema_slow[-1]),
            macd=_sign_vote(macd_hist[-1]),
            rsi=_band_vote(rsi[-1], low=30.0, high=70.0),
            bollinger=_band_vote(_percent_b(close, bb_upper[-1], bb_lower[-1]),
                                 low=0.05, high=0.95),
        )
        score = score_votes(votes, self.weights)
        reason = (
            f"consensus {score:.2f} (ema{_VOTE_CHAR[votes.ema]} macd{_VOTE_CHAR[votes.macd]}"
            f" rsi{_VOTE_CHAR[votes.rsi]} bb{_VOTE_CHAR[votes.bollinger]})"
        )

        if st.direction == 0:
            if abs(score) >= self.threshold and self._regime_ok(closes, ema_slow):
                st.direction = 1 if score > 0 else -1
                action = SignalAction.ENTER_LONG if score > 0 else SignalAction.ENTER_SHORT
                return [Signal(symbol=symbol, action=action, strength=abs(score),
                               reason=reason, ts_ns=ts_ns, strategy=self.name)]
            return []
        # Hysteresis: exit only when the score falls back through threshold/2
        # against the tracked direction (a hard sign flip crosses it too).
        # Exits are exempt from the regime filter.
        exit_band = self.threshold / 2.0
        if (st.direction > 0 and score < exit_band) or (st.direction < 0 and score > -exit_band):
            st.direction = 0
            return [Signal(symbol=symbol, action=SignalAction.EXIT, strength=1.0,
                           reason=reason, ts_ns=ts_ns, strategy=self.name)]
        return []

    def _regime_ok(self, closes: list[float], ema_slow: list[float | None]) -> bool:
        """Entries only: block chop (low realized vol) and flat drift (no slope)."""
        n = len(closes)
        if n < 21 or len(ema_slow) < 6:
            return False
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(n - 20, n)]
        mean = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
        if vol <= self.vol_floor:
            return False
        e_now, e_then = ema_slow[-1], ema_slow[-6]
        if e_now is None or e_then is None or closes[-1] <= 0.0:
            return False
        slope_per_bar = (e_now - e_then) / 5.0
        return abs(slope_per_bar) / closes[-1] > self.slope_min


# ---- pure vote helpers ----------------------------------------------------


def _cmp_vote(fast: float | None, slow: float | None) -> int:
    if fast is None or slow is None:
        return 0
    return 1 if fast > slow else (-1 if fast < slow else 0)


def _sign_vote(value: float | None) -> int:
    if value is None:
        return 0
    return 1 if value > 0.0 else (-1 if value < 0.0 else 0)


def _band_vote(value: float | None, low: float, high: float) -> int:
    """Mean-revert vote: below `low` -> +1 (long), above `high` -> -1 (short)."""
    if value is None:
        return 0
    return 1 if value < low else (-1 if value > high else 0)


def _percent_b(close: float, upper: float | None, lower: float | None) -> float | None:
    if upper is None or lower is None or upper <= lower:
        return None
    return (close - lower) / (upper - lower)
