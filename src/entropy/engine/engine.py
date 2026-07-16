from __future__ import annotations

import heapq
from collections import deque

import msgspec

from ..config import EngineConfig
from .breadth import BreadthTracker
from .events import DownMove, Event, NewHigh, NewLow, SnapDrop, Spike, UpMove, WindowName
from .leaderboard import LeaderRow
from .windows import MomentumHorizon, MonotonicExtreme, SessionExtreme

_WIN_ORDER = (WindowName.W0, WindowName.W1, WindowName.W2)


class _Tape:
    __slots__ = ("maxw", "minw", "session", "mom", "last_ts", "last_price",
                 "nh_count", "nl_count", "nh_by_win", "nl_by_win",
                 "last_mom_pct", "_cooldown")

    def __init__(self, cfg: EngineConfig) -> None:
        self.maxw = [MonotonicExtreme(cfg.windows_ns[w.value], +1) for w in _WIN_ORDER]
        self.minw = [MonotonicExtreme(cfg.windows_ns[w.value], -1) for w in _WIN_ORDER]
        self.session = SessionExtreme()
        self.mom = MomentumHorizon(int(cfg.momentum_horizon_s * 1_000_000_000))
        self.last_ts = 0
        self.last_price = 0.0
        self.nh_count = 0
        self.nl_count = 0
        # rolling deques of timestamps for new-high / new-low events per window
        # (indexed by _WIN_ORDER)
        self.nh_by_win: list[deque[int]] = [deque() for _ in range(3)]
        self.nl_by_win: list[deque[int]] = [deque() for _ in range(3)]
        self.last_mom_pct = 0.0
        self._cooldown: dict[str, int] = {}


class BreadthSnapshot(msgspec.Struct, frozen=True):
    sell_pct: float
    buy_pct: float
    raw_hz: float
    prev30s_rate: float
    accel: str
    nh_counts: dict[str, int]
    nl_counts: dict[str, int]


class TickerGroup(msgspec.Struct, frozen=True):
    """Top symbols by new-high/low activity within one rolling window — drives
    the header ticker strip (e.g. '15m: GWW 15  APP 13  SPOT 12')."""
    window: str
    entries: tuple[tuple[str, int], ...]   # (symbol, combined nh+nl count in window)


class EngineSnapshot(msgspec.Struct, frozen=True):
    ts_ns: int
    breadth: BreadthSnapshot
    top_movers: tuple[LeaderRow, ...]
    new_highs: tuple[LeaderRow, ...]
    new_lows: tuple[LeaderRow, ...]
    ticker: tuple[TickerGroup, ...]


class Engine:
    def __init__(self, config: EngineConfig | None = None) -> None:
        self._tapes: dict[str, _Tape] = {}
        self._seen: set[str] = set()
        self.cfg = config or EngineConfig()   # property setter caches the scalars
        self.breadth = BreadthTracker(self.cfg.breadth_window_s, self.cfg.accel_eps)
        self._prev_event_rate = 0.0
        self._accel_label = "steady"
        self._trade_seq = 0        # monotonic; bumps on every on_trade
        self._accel_sample_seq = -1

    @property
    def cfg(self) -> EngineConfig:
        return self._cfg

    @cfg.setter
    def cfg(self, value: EngineConfig) -> None:
        # Hot-apply: the UI's non-timeframe settings path reassigns engine.cfg
        # and expects it to take effect live. Refresh the cached momentum
        # scalars and re-span existing tapes' rolling windows (buffers are kept;
        # they prune lazily against the new spans on the next trade).
        self._cfg = value
        self._horizon_s = value.momentum_horizon_s
        self._cool_ns = value.momentum_cooldown_ns
        mom_span = int(value.momentum_horizon_s * 1_000_000_000)
        spans = [value.windows_ns[w.value] for w in _WIN_ORDER]
        for t in self._tapes.values():
            t.mom.set_span(mom_span)
            for me, span in zip(t.maxw, spans, strict=True):
                me.span_ns = span
            for me, span in zip(t.minw, spans, strict=True):
                me.span_ns = span

    def on_trade(  # noqa: PLR0912
        self, symbol: str, price: float, amount: float, side: str, ts_ns: int
    ) -> list[Event]:
        t = self._tapes.get(symbol)
        if t is None:
            t = _Tape(self.cfg)
            self._tapes[symbol] = t
        ts = ts_ns if ts_ns >= t.last_ts else t.last_ts   # non-decreasing clamp
        t.last_ts = ts
        self._trade_seq += 1
        self.breadth.tick(ts)
        self.breadth.add_trade(side, amount, ts)
        first = symbol not in self._seen
        events: list[Event] = []
        if first:
            self._seen.add(symbol)
            for me in t.maxw:
                me.step(ts, price)
            for me in t.minw:
                me.step(ts, price)
            t.session.step(price)
            t.mom.push(ts, price)
            t.last_price = price
            return events
        for i, (w, me) in enumerate(zip(_WIN_ORDER, t.maxw, strict=False)):
            me.evict(ts)            # evict BEFORE peek so prev_extreme reflects the live window
            prior = me.peek()
            if me.step(ts, price):
                events.append(
                    NewHigh(symbol=symbol, ts_ns=ts, price=price, window=w, prev_extreme=prior)
                )
                t.nh_count += 1
                dq = t.nh_by_win[i]
                dq.append(ts)
                # Evict here (not in snapshot) so headless runs that never
                # snapshot keep the deque bounded by the window contents.
                cutoff = ts - self.cfg.windows_ns[w.value]
                while dq and dq[0] < cutoff:
                    dq.popleft()
        for i, (w, me) in enumerate(zip(_WIN_ORDER, t.minw, strict=False)):
            me.evict(ts)            # evict BEFORE peek (the evict inside step() is then a no-op)
            prior = me.peek()
            if me.step(ts, price):
                events.append(
                    NewLow(symbol=symbol, ts_ns=ts, price=price, window=w, prev_extreme=prior)
                )
                t.nl_count += 1
                dq = t.nl_by_win[i]
                dq.append(ts)
                cutoff = ts - self.cfg.windows_ns[w.value]
                while dq and dq[0] < cutoff:
                    dq.popleft()
        sh, sl = t.session.step(price)
        if sh:
            events.append(NewHigh(symbol=symbol, ts_ns=ts, price=price, window=WindowName.SESSION))
            t.nh_count += 1
        if sl:
            events.append(NewLow(symbol=symbol, ts_ns=ts, price=price, window=WindowName.SESSION))
            t.nl_count += 1
        ref = t.mom.push(ts, price)
        if t.mom.has_anchor(ts) and ref > 0:
            pct = (price - ref) / ref * 100.0
            kind = self._classify(pct)
            cool_key = -self._cool_ns
            if kind is not None and ts - t._cooldown.get(kind.__name__, cool_key) >= self._cool_ns:
                events.append(kind(symbol=symbol, ts_ns=ts, price=price, pct=pct,
                                   horizon_s=self._horizon_s, ref_price=ref))
                t._cooldown[kind.__name__] = ts
            t.last_mom_pct = pct
        t.last_price = price
        self.breadth.events(ts, len(events))
        return events

    def _classify(
        self, pct: float
    ) -> type[Spike] | type[UpMove] | type[SnapDrop] | type[DownMove] | None:
        c = self.cfg
        if pct >= c.spike_pct:
            return Spike
        if pct >= c.upmove_pct:
            return UpMove
        if pct <= -c.snapdrop_pct:
            return SnapDrop
        if pct <= -c.downmove_pct:
            return DownMove
        return None

    def snapshot(self) -> EngineSnapshot:
        k = self.cfg.leaderboard_k
        items = list(self._tapes.items())

        def mk(sel: list[tuple[str, _Tape]], count_fn: object) -> tuple[LeaderRow, ...]:
            from collections.abc import Callable
            fn: Callable[[_Tape], int] = count_fn  # type: ignore[assignment]
            return tuple(
                LeaderRow(symbol=s, count=fn(tp), price=tp.last_price,
                          pct_chg=tp.session.pct_chg(tp.last_price) * 100)
                for s, tp in sel
            )

        top = heapq.nlargest(k, items, key=lambda kv: abs(kv[1].session.pct_chg(kv[1].last_price)))
        highs = heapq.nlargest(k, items, key=lambda kv: kv[1].nh_count)
        lows = heapq.nlargest(k, items, key=lambda kv: kv[1].nl_count)
        last_ts = max((t.last_ts for t in self._tapes.values()), default=0)
        # The accel flag is a rate delta between successive SAMPLES of the tape,
        # so advance the prev-rate state only when the tape itself advanced —
        # back-to-back snapshots with no new trades are then identical.
        if self._trade_seq != self._accel_sample_seq:
            self._accel_label = self.breadth.accel(self._prev_event_rate)
            self._prev_event_rate = self.breadth.event_rate()
            self._accel_sample_seq = self._trade_seq
        # Per-window aggregate new-high / new-low symbol-activity counts (dual gauges)
        # and the top-symbol ticker groups (the "<win>: SYM n  SYM n" strip).
        # Stamps older than the window (relative to the global clock) are evicted
        # here before len(). Eviction is IDEMPOTENT — it only drops stamps that
        # count zero — so back-to-back snapshots stay identical, and headless
        # boundedness is already guaranteed by the on_trade eviction. Evict+len
        # keeps the per-snapshot cost O(evicted), not O(in-window stamps).
        nh_counts: dict[str, int] = {}
        nl_counts: dict[str, int] = {}
        ticker: list[TickerGroup] = []
        tk = 6  # symbols shown per window in the strip

        for i, w in enumerate(_WIN_ORDER):
            cutoff = last_ts - self.cfg.windows_ns[w.value]
            nh_tot = 0
            nl_tot = 0
            active: list[tuple[str, int]] = []
            for s, tp in items:
                dq_h = tp.nh_by_win[i]
                while dq_h and dq_h[0] < cutoff:
                    dq_h.popleft()
                dq_l = tp.nl_by_win[i]
                while dq_l and dq_l[0] < cutoff:
                    dq_l.popleft()
                h = len(dq_h)
                lo = len(dq_l)
                nh_tot += h
                nl_tot += lo
                if h + lo:
                    active.append((s, h + lo))
            label = self.cfg.window_labels[i]
            nh_counts[label] = nh_tot
            nl_counts[label] = nl_tot
            entries = tuple(heapq.nlargest(tk, active, key=lambda kv: kv[1]))
            ticker.append(TickerGroup(window=label, entries=entries))

        breadth = BreadthSnapshot(
            sell_pct=self.breadth.sell_pct(), buy_pct=self.breadth.buy_pct(),
            raw_hz=self.breadth.raw_hz(), prev30s_rate=self._prev_event_rate,
            accel=self._accel_label, nh_counts=nh_counts, nl_counts=nl_counts)
        return EngineSnapshot(
            ts_ns=last_ts, breadth=breadth,
            top_movers=mk(top, lambda tp: tp.nh_count + tp.nl_count),
            new_highs=mk(highs, lambda tp: tp.nh_count),
            new_lows=mk(lows, lambda tp: tp.nl_count),
            ticker=tuple(ticker))

    def quote(self, symbol: str) -> tuple[float, float] | None:
        """Last price and session %-change for one symbol, or None if unseen.

        Lets the UI always show fixed reference quotes (e.g. SPY/QQQ/IWM) that
        are too low-volatility to appear in the leaderboards.
        """
        t = self._tapes.get(symbol)
        if t is None:
            return None
        return t.last_price, t.session.pct_chg(t.last_price) * 100

    def reset_session(self, ts_ns: int | None = None) -> None:
        # Full tape rebuild: post-reset trades must see exactly what a fresh
        # engine would — the rolling MonotonicExtreme windows and the momentum
        # buffer must not leak across the session boundary (the old partial
        # reset re-seeded "first sightings" against still-populated windows).
        # Breadth is intentionally untouched (unchanged semantics).
        self._tapes.clear()
        self._seen.clear()
