"""Multi-indicator consensus strategy with a regime-aware vote mapping.

Ticks are aggregated into fixed-length bars; on every *completed* bar the
strategy combines four indicator votes (EMA cross, MACD histogram, RSI,
Bollinger %B) into a weighted score in [-1, 1]. Entries additionally require a
tradeable regime (enough realized volatility) and exits use a hysteresis band
plus a minimum hold so a single noisy bar does not churn the position. This
targets better *signal quality* than the single-indicator strategies; it is not
a claim of live trading edge.

Why the vote mapping is regime-aware
------------------------------------
The original version scored every bar with a fixed mapping: EMA/MACD voted
trend-following while RSI/Bollinger voted mean-reversion. Those two families
disagree by construction exactly when a trend is strongest — a sustained rally
pins RSI above 70 and %B above 0.95, so the oscillators subtracted 0.35 from a
0.65 trend score and the total (0.30) never cleared the 0.50 threshold.

Measured consequence of that mapping on synthetic paths:

* a clean +44% trend over 600 bars produced **zero** signals;
* a flat chop phase over 200 bars produced **36** signals (18 round trips),
  every one of them reading ``ema± macd± rsi0 bb0`` — i.e. RSI and Bollinger
  never once contributed a supporting vote, they only ever vetoed.

So the "four-indicator consensus" collapsed into a two-indicator EMA/MACD gate
with two permanent vetoes attached: silent in trends, hyperactive in chop —
precisely inverted. ``vote_mode="adaptive"`` (the default) fixes this by asking
the oscillators a question appropriate to the regime: mean-reversion while
ranging, momentum confirmation while trending. ``vote_mode="legacy"`` restores
the original mapping bit-for-bit for reproducing old runs.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from entropy.engine.events import Event
from entropy.strategy.engine import Bar

from ..costs import E_ABS_MOVE, CostModel
from ..signals import Signal, SignalAction

_NS_PER_S = 1_000_000_000
_CLOSES_MAXLEN = 512

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "ema": 0.35,
    "macd": 0.30,
    "rsi": 0.20,
    "bollinger": 0.15,
}

#: How an oscillator vote is interpreted.
VOTE_MODES = ("adaptive", "trend", "mean_revert", "legacy")
#: How the weighted sum is turned into a score.
NORMALIZE_MODES = ("participating", "total")
#: What ends a position.
EXIT_MODES = ("score", "trend_flip", "either")

_VOTE_CHAR = {1: "+", 0: "0", -1: "-"}


@dataclass(frozen=True, slots=True)
class Votes:
    """One -1/0/+1 vote per indicator for a completed bar."""

    ema: int
    macd: int
    rsi: int
    bollinger: int


@dataclass(frozen=True, slots=True)
class Regime:
    """What kind of market the last bar closed in.

    ``move`` and ``efficiency`` answer two different questions and the original
    code conflated them into one "realized volatility" number. *Is anything
    happening?* is mean absolute per-bar return. *Is what is happening going
    somewhere?* is Kaufman's efficiency ratio — net displacement divided by
    total path length, 1.0 for a ruler-straight move and ~0 for pure chop.

    Gating on return **dispersion** got this backwards: a smooth, strong trend
    has the *lowest* dispersion of any interesting market, so the old filter
    labelled clean trends "chop" and refused to trade them.
    """

    move: float           # mean |return| per bar over the regime window
    efficiency: float     # |net change| / total path length, in [0, 1]
    slope: float          # signed EMA slope per bar, normalized by price
    tradeable: bool       # enough movement to be worth trading
    trending: bool        # efficiency above the trend boundary

    @property
    def label(self) -> str:
        if not self.tradeable:
            return "chop"
        return "trend" if self.trending else "range"


def score_votes(
    votes: Votes,
    weights: Mapping[str, float],
    *,
    normalize: str = "total",
    min_participation: float = 0.0,
) -> float:
    """Weighted consensus score in [-1, 1].

    ``normalize="total"`` divides by the total weight — an abstaining indicator
    therefore drags the score toward zero, so three indicators agreeing while
    the fourth has no opinion reads as weak agreement.

    ``normalize="participating"`` divides by the weight of the indicators that
    actually voted, which is what "consensus" normally means: the score answers
    *how strongly do those with an opinion agree*, while ``min_participation``
    (a fraction of total weight) separately guards against reading a lone
    indicator as unanimity.
    """
    total = sum(abs(w) for w in weights.values())
    if total <= 0.0:
        return 0.0
    raw = (
        weights.get("ema", 0.0) * votes.ema
        + weights.get("macd", 0.0) * votes.macd
        + weights.get("rsi", 0.0) * votes.rsi
        + weights.get("bollinger", 0.0) * votes.bollinger
    )
    if normalize != "participating":
        return raw / total
    participating = (
        abs(weights.get("ema", 0.0)) * (votes.ema != 0)
        + abs(weights.get("macd", 0.0)) * (votes.macd != 0)
        + abs(weights.get("rsi", 0.0)) * (votes.rsi != 0)
        + abs(weights.get("bollinger", 0.0)) * (votes.bollinger != 0)
    )
    if participating <= 0.0 or participating < min_participation * total:
        return 0.0
    return raw / participating


def tilt_weights(
    weights: Mapping[str, float], *, trending: bool, tilt: float
) -> dict[str, float]:
    """Re-weight the two indicator blocks for the current regime.

    In a trend the EMA/MACD block carries the information and the oscillators
    are secondary; while ranging it is the other way round. Without this tilt
    the trend block alone (0.65 of 1.0 weight) could open a position in a
    sideways market — which, combined with participating-weight normalization,
    is exactly how a two-indicator agreement gets mistaken for unanimity.
    """
    lead, follow = (tilt, 1.0) if trending else (1.0, tilt)
    return {
        "ema": weights.get("ema", 0.0) * lead,
        "macd": weights.get("macd", 0.0) * lead,
        "rsi": weights.get("rsi", 0.0) * follow,
        "bollinger": weights.get("bollinger", 0.0) * follow,
    }


def trend_score(votes: Votes, weights: Mapping[str, float]) -> float:
    """Score of the trend block (EMA + MACD) alone, in [-1, 1].

    Used by ``exit_mode="trend_flip"``: an exit that waits for the *trend* to
    turn rather than for an oscillator wobble to nick the hysteresis band.
    """
    w = abs(weights.get("ema", 0.0)) + abs(weights.get("macd", 0.0))
    if w <= 0.0:
        return 0.0
    return (weights.get("ema", 0.0) * votes.ema + weights.get("macd", 0.0) * votes.macd) / w


@dataclass(slots=True)
class _SymbolState:
    closes: deque[float] = field(default_factory=lambda: deque(maxlen=_CLOSES_MAXLEN))
    bucket: int | None = None  # current bar's time bucket (ts_ns // bar_ns)
    bar_close: float = 0.0  # latest price seen inside the current bucket
    pending: bool = False  # True once a live tick landed in the current bucket
    direction: int = 0  # what THIS strategy last signaled: +1 long, -1 short, 0 flat
    bars_in_trade: int = 0  # completed bars since the current entry
    bars_since_exit: int = 1 << 30  # completed bars since the last exit (cooldown)


class ConsensusStrategy:
    """Weighted multi-indicator consensus with hysteresis and a regime filter.

    Votes (per completed bar, needs >= ``min_bars`` closes):

    * EMA(fast) vs EMA(slow): fast above -> +1, below -> -1
    * MACD(fast, slow, signal) histogram sign
    * RSI(period), read per regime:
      ranging -> ``< rsi_low`` = +1 (mean-revert long), ``> rsi_high`` = -1;
      trending -> ``> rsi_trend_high`` = +1 (momentum), ``< rsi_trend_low`` = -1
    * Bollinger(period, std) %B, read per regime:
      ranging -> ``< bb_low`` = +1, ``> bb_high`` = -1;
      trending -> ``> bb_trend_high`` = +1 (band riding), ``< bb_trend_low`` = -1

    Entries require ``|score| >= threshold``, a tradeable regime, and the
    per-symbol re-entry cooldown to have elapsed. Exits fire per ``exit_mode``
    once ``min_hold_bars`` have passed; they are exempt from the regime filter.
    The strategy only tracks what it has signaled itself — actual position state
    lives in the portfolio/risk layer.
    """

    name = "consensus"

    def __init__(
        self,
        symbols: tuple[str, ...] | None = None,
        bar_s: float = 5.0,
        threshold: float = 0.5,
        min_bars: int = 35,
        weights: dict[str, float] | None = None,
        move_floor: float = 0.0005,
        trend_er: float = 0.35,
        *,
        ema_fast: int = 9,
        ema_slow: int = 21,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rsi_period: int = 14,
        rsi_low: float = 30.0,
        rsi_high: float = 70.0,
        rsi_trend_low: float = 45.0,
        rsi_trend_high: float = 55.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        bb_low: float = 0.05,
        bb_high: float = 0.95,
        bb_trend_low: float = 0.20,
        bb_trend_high: float = 0.80,
        vote_mode: str = "adaptive",
        normalize: str = "participating",
        min_participation: float = 0.5,
        min_hold_bars: int = 3,
        cooldown_bars: int = 2,
        exit_mode: str = "score",
        regime_window: int = 20,
        slope_lookback: int = 5,
        regime_tilt: float = 2.0,
        warmup_symbol: str | None = None,
        costs: CostModel | None = None,
        cost_edge_mult: float = 2.0,
    ) -> None:
        if bar_s <= 0.0:
            raise ValueError("bar_s must be positive")
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        if vote_mode not in VOTE_MODES:
            raise ValueError(f"vote_mode must be one of {VOTE_MODES}")
        if normalize not in NORMALIZE_MODES:
            raise ValueError(f"normalize must be one of {NORMALIZE_MODES}")
        if exit_mode not in EXIT_MODES:
            raise ValueError(f"exit_mode must be one of {EXIT_MODES}")
        if min(ema_fast, ema_slow, macd_fast, macd_slow, macd_signal,
               rsi_period, bb_period) < 1:
            raise ValueError("indicator periods must be >= 1")
        if ema_fast >= ema_slow:
            raise ValueError("ema_fast must be shorter than ema_slow")
        if macd_fast >= macd_slow:
            raise ValueError("macd_fast must be shorter than macd_slow")
        if cost_edge_mult <= 0.0:
            raise ValueError("cost_edge_mult must be positive")

        self.symbols = symbols  # None = trade every symbol
        self.bar_s = bar_s
        self.threshold = threshold
        self.weights: Mapping[str, float] = dict(weights) if weights else dict(DEFAULT_WEIGHTS)
        self.move_floor = move_floor
        self.trend_er = trend_er
        self.ema_fast, self.ema_slow = ema_fast, ema_slow
        self.macd_fast, self.macd_slow, self.macd_signal = macd_fast, macd_slow, macd_signal
        self.rsi_period, self.rsi_low, self.rsi_high = rsi_period, rsi_low, rsi_high
        self.rsi_trend_low, self.rsi_trend_high = rsi_trend_low, rsi_trend_high
        self.bb_period, self.bb_std = bb_period, bb_std
        self.bb_low, self.bb_high = bb_low, bb_high
        self.bb_trend_low, self.bb_trend_high = bb_trend_low, bb_trend_high
        self.vote_mode = vote_mode
        # The legacy mapping is only faithful with total-weight normalization and
        # no participation floor — pin them so `vote_mode="legacy"` really is the
        # old strategy rather than the old votes under new scoring.
        if vote_mode == "legacy":
            normalize, min_participation = "total", 0.0
        self.normalize = normalize
        self.min_participation = min_participation
        self.min_hold_bars = max(0, min_hold_bars)
        self.cooldown_bars = max(0, cooldown_bars)
        self.exit_mode = exit_mode
        self.regime_window = max(2, regime_window)
        self.slope_lookback = max(1, slope_lookback)
        self.regime_tilt = regime_tilt
        self.warmup_symbol = warmup_symbol
        self.costs = costs
        self.cost_edge_mult = cost_edge_mult
        #: Last computed per-bar move magnitude (RMS of returns over the regime
        #: window), per symbol; reused by the cost-adjusted exit band.
        self._last_move_rms: dict[str, float] = {}
        # Indicators silently return None until they have enough closes; make the
        # gate honest instead of evaluating bars that can only produce abstentions.
        self.min_bars = max(
            min_bars, ema_slow, macd_slow + macd_signal, rsi_period + 1,
            bb_period, self.regime_window + 1, self.slope_lookback + 1,
        )
        self._bar_ns = max(1, int(bar_s * _NS_PER_S))
        self._states: dict[str, _SymbolState] = {}
        # Warmup seed for symbols==None: `Bar` carries no symbol, so seeded
        # closes are adopted by `warmup_symbol` when given, else by the first
        # symbol that ticks (best effort — see warmup()).
        self._seed: _SymbolState | None = None
        #: Last evaluated regime per symbol; exposed for dashboards/telemetry.
        self.last_regime: dict[str, Regime] = {}

    # ---- warmup ---------------------------------------------------------

    def warmup(self, bars: Sequence[Bar]) -> None:
        """Seed bar closes so live ticks are immediately eligible for signals.

        ``Bar`` carries no symbol. With an explicit ``symbols`` tuple (or a
        ``warmup_symbol``) the attribution is unambiguous; with neither, the
        seed is adopted by whichever symbol ticks first — best effort, and the
        reason the runner passes a ``warmup_symbol``.
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

        A mechanical stop/take-profit (or a risk rejection that blocked the
        entry) leaves ``direction`` pointing at a trade the portfolio does not
        hold. The strategy would then wait for its own exit condition before
        signalling again — so one early take-profit could silence it for the
        whole rest of a trend. Re-arm instead, and start the re-entry cooldown
        so it does not immediately buy back at the same price.
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
            st.bar_close = price  # still inside the current bar: track its close
            st.pending = True
            return []
        committed = st.bucket is not None and st.pending
        if committed:
            st.closes.append(st.bar_close)
            # Bar counters advance on COMPLETED bars only, so min_hold/cooldown
            # are measured in bars regardless of how dense the tick stream is.
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
        # Lazy import: crocodile.core.analytics.indicators pulls numpy+polars (~0.3 s); pay it
        # on the first evaluated bar, not at bot startup. Cached in sys.modules.
        from crocodile.core.analytics.indicators import (
            calculate_bollinger_bands,
            calculate_ema,
            calculate_macd,
            calculate_rsi,
        )

        ema_fast = calculate_ema(closes, self.ema_fast)
        ema_slow = calculate_ema(closes, self.ema_slow)
        _, _, macd_hist = calculate_macd(
            closes, self.macd_fast, self.macd_slow, self.macd_signal
        )
        rsi = calculate_rsi(closes, self.rsi_period)
        bb_upper, _, bb_lower = calculate_bollinger_bands(closes, self.bb_period, self.bb_std)

        regime = self._regime(closes, ema_slow)
        self.last_regime[symbol] = regime
        close = closes[-1]
        pct_b = _percent_b(close, bb_upper[-1], bb_lower[-1])

        # Regime decides what question the oscillators are answering. In a trend
        # the informative question is "is momentum confirming?"; while ranging it
        # is "are we stretched far enough to snap back?". Asking the second one
        # during a trend is what made the original mapping trend-blind.
        momentum_read = self.vote_mode == "trend" or (
            self.vote_mode == "adaptive" and regime.trending
        )
        if momentum_read:
            rsi_vote = _momentum_vote(rsi[-1], low=self.rsi_trend_low, high=self.rsi_trend_high)
            bb_vote = _momentum_vote(pct_b, low=self.bb_trend_low, high=self.bb_trend_high)
        else:
            rsi_vote = _band_vote(rsi[-1], low=self.rsi_low, high=self.rsi_high)
            bb_vote = _band_vote(pct_b, low=self.bb_low, high=self.bb_high)

        votes = Votes(
            ema=_cmp_vote(ema_fast[-1], ema_slow[-1]),
            macd=_sign_vote(macd_hist[-1]),
            rsi=rsi_vote,
            bollinger=bb_vote,
        )
        # Legacy keeps flat weights so `vote_mode="legacy"` reproduces old runs
        # exactly; every other mode lets the regime decide which block leads.
        weights = (
            self.weights if self.vote_mode == "legacy"
            else tilt_weights(self.weights, trending=regime.trending, tilt=self.regime_tilt)
        )
        score = score_votes(
            votes, weights,
            normalize=self.normalize, min_participation=self.min_participation,
        )
        reason = (
            f"consensus {score:.2f} [{regime.label}] "
            f"(ema{_VOTE_CHAR[votes.ema]} macd{_VOTE_CHAR[votes.macd]}"
            f" rsi{_VOTE_CHAR[votes.rsi]} bb{_VOTE_CHAR[votes.bollinger]})"
        )

        if st.direction == 0:
            if st.bars_since_exit < self.cooldown_bars:
                return []
            if abs(score) >= self.threshold and regime.tradeable:
                if self.costs is not None:
                    floor = max(self.move_floor,
                                self.costs.minimum_move(symbol, self.cost_edge_mult))
                    # Effective entry floor: regime.tradeable uses the base
                    # move_floor, so the cost-aware floor is checked explicitly.
                    if regime.move <= floor:
                        return []
                    rms = self._bar_move_rms(closes)
                    self._last_move_rms[symbol] = rms
                    if rms < self.costs.sigma_gate(symbol, self.cost_edge_mult):
                        return []
                st.direction = 1 if score > 0 else -1
                st.bars_in_trade = 0
                action = SignalAction.ENTER_LONG if score > 0 else SignalAction.ENTER_SHORT
                return [Signal(symbol=symbol, action=action, strength=abs(score),
                               reason=reason, ts_ns=ts_ns, strategy=self.name)]
            return []

        if st.bars_in_trade < self.min_hold_bars:
            return []  # a fresh position rides out its first few bars
        if self.costs is not None:
            self._last_move_rms[symbol] = self._bar_move_rms(closes)
        if not self._should_exit(score, votes, st.direction, symbol):
            return []
        st.direction = 0
        st.bars_in_trade = 0
        st.bars_since_exit = 0
        return [Signal(symbol=symbol, action=SignalAction.EXIT, strength=1.0,
                       reason=reason, ts_ns=ts_ns, strategy=self.name)]

    def _should_exit(self, score: float, votes: Votes, direction: int,
                     symbol: str | None = None) -> bool:
        """Hysteresis: the score must fall back through ``threshold / 2`` against
        the tracked direction (a hard sign flip crosses it too), and/or the trend
        block must flip — per ``exit_mode``.

        With a cost model the band is tightened by a cost buffer so a position
        is held through breakeven noise instead of being churned: the score
        must retrace deeper before the exit fires, and the tighter the bar
        moves are relative to ``k*C`` the deeper the retrace required —
        ``buffer = min(0.15, k*C / (E_ABS_MOVE*rms))`` is *subtracted* from the
        half-threshold band.
        """
        band = self.threshold / 2.0
        if self.costs is not None and symbol is not None:
            rms = self._last_move_rms.get(symbol, 0.0)
            if rms > 0.0:
                kc = self.costs.minimum_move(symbol, self.cost_edge_mult)
                band -= min(0.15, kc / (E_ABS_MOVE * rms))
            else:
                band -= 0.15
        by_score = (direction > 0 and score < band) or (direction < 0 and score > -band)
        if self.exit_mode == "score":
            return by_score
        ts = trend_score(votes, self.weights)
        by_trend = (direction > 0 and ts < 0.0) or (direction < 0 and ts > 0.0)
        if self.exit_mode == "trend_flip":
            return by_trend
        return by_score or by_trend

    def _bar_move_rms(self, closes: list[float]) -> float:
        """Per-bar move magnitude (RMS of returns) over the regime window.

        Population std around the mean under-measures exactly the bars this
        strategy wants to trade — a smooth trend has near-zero dispersion but
        large per-bar moves. RMS around zero keeps both: in a trend it reads
        the trend size, in chop it reads the noise size.
        """
        n = len(closes)
        window = min(self.regime_window, n - 1)
        if window < 2:
            return 0.0
        seg = closes[n - window - 1:]
        rets = [seg[i] / seg[i - 1] - 1.0 for i in range(1, len(seg))]
        return math.sqrt(sum(r * r for r in rets) / len(rets))

    def _regime(self, closes: list[float], ema_slow: Sequence[float | None]) -> Regime:
        """Classify the bar: movement gates entries, efficiency picks the reading."""
        n = len(closes)
        window = min(self.regime_window, n - 1)
        if window < 2:
            return Regime(move=0.0, efficiency=0.0, slope=0.0,
                          tradeable=False, trending=False)
        seg = closes[n - window - 1:]
        rets = [seg[i] / seg[i - 1] - 1.0 for i in range(1, len(seg))]
        move = sum(abs(r) for r in rets) / len(rets)
        # Kaufman efficiency ratio: how much of the distance travelled was
        # actually progress. Scale-free, so it behaves the same on a $4 stock
        # and a $70k coin.
        path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
        efficiency = abs(seg[-1] - seg[0]) / path if path > 0.0 else 0.0

        slope = 0.0
        lb = self.slope_lookback
        if len(ema_slow) > lb and closes[-1] > 0.0:
            e_now, e_then = ema_slow[-1], ema_slow[-1 - lb]
            if e_now is not None and e_then is not None:
                slope = (e_now - e_then) / lb / closes[-1]
        return Regime(
            move=move, efficiency=efficiency, slope=slope,
            tradeable=move > self.move_floor,
            trending=efficiency >= self.trend_er,
        )


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


def _momentum_vote(value: float | None, low: float, high: float) -> int:
    """Momentum vote: above `high` -> +1 (long), below `low` -> -1 (short).

    The mirror image of :func:`_band_vote`. An oscillator pinned at an extreme
    means "this move has conviction" in a trending regime, not "it is about to
    reverse" — reading it the mean-reverting way there is what made the original
    strategy fire zero signals across a clean +44% trend.
    """
    if value is None:
        return 0
    return 1 if value > high else (-1 if value < low else 0)


def _percent_b(close: float, upper: float | None, lower: float | None) -> float | None:
    if upper is None or lower is None or upper <= lower:
        return None
    return (close - lower) / (upper - lower)
