from __future__ import annotations

import heapq

import msgspec

from ..config import EngineConfig
from .breadth import BreadthTracker
from .events import DownMove, Event, NewHigh, NewLow, SnapDrop, Spike, UpMove, WindowName
from .leaderboard import LeaderRow
from .windows import MomentumHorizon, MonotonicExtreme, SessionExtreme

_WIN_ORDER = (WindowName.S30, WindowName.M1, WindowName.M5, WindowName.M20)


class _Tape:
    __slots__ = ("maxw", "minw", "session", "mom", "last_ts", "last_price",
                 "nh_count", "nl_count", "last_mom_pct", "_cooldown")

    def __init__(self, cfg: EngineConfig) -> None:
        self.maxw = [MonotonicExtreme(cfg.windows_ns[w.value], +1) for w in _WIN_ORDER]
        self.minw = [MonotonicExtreme(cfg.windows_ns[w.value], -1) for w in _WIN_ORDER]
        self.session = SessionExtreme()
        self.mom = MomentumHorizon(int(cfg.momentum_horizon_s * 1_000_000_000))
        self.last_ts = 0
        self.last_price = 0.0
        self.nh_count = 0
        self.nl_count = 0
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


class EngineSnapshot(msgspec.Struct, frozen=True):
    ts_ns: int
    breadth: BreadthSnapshot
    top_movers: tuple[LeaderRow, ...]
    new_highs: tuple[LeaderRow, ...]
    new_lows: tuple[LeaderRow, ...]


class Engine:
    def __init__(self, config: EngineConfig | None = None) -> None:
        self.cfg = config or EngineConfig()
        self.breadth = BreadthTracker(self.cfg.breadth_window_s, self.cfg.accel_eps)
        self._tapes: dict[str, _Tape] = {}
        self._seen: set[str] = set()
        self._prev_event_rate = 0.0
        self._horizon_s = self.cfg.momentum_horizon_s
        self._cool_ns = self.cfg.momentum_cooldown_ns

    def on_trade(  # noqa: PLR0912
        self, symbol: str, price: float, amount: float, side: str, ts_ns: int
    ) -> list[Event]:
        t = self._tapes.get(symbol)
        if t is None:
            t = _Tape(self.cfg)
            self._tapes[symbol] = t
        ts = ts_ns if ts_ns >= t.last_ts else t.last_ts   # non-decreasing clamp
        t.last_ts = ts
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
        for w, me in zip(_WIN_ORDER, t.maxw, strict=False):
            prior = me.peek()
            if me.step(ts, price):
                events.append(
                    NewHigh(symbol=symbol, ts_ns=ts, price=price, window=w, prev_extreme=prior)
                )
                t.nh_count += 1
        for w, me in zip(_WIN_ORDER, t.minw, strict=False):
            prior = me.peek()
            if me.step(ts, price):
                events.append(
                    NewLow(symbol=symbol, ts_ns=ts, price=price, window=w, prev_extreme=prior)
                )
                t.nl_count += 1
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
        rate = self.breadth.event_rate()
        accel = self.breadth.accel(self._prev_event_rate)
        self._prev_event_rate = rate
        breadth = BreadthSnapshot(
            sell_pct=self.breadth.sell_pct(), buy_pct=self.breadth.buy_pct(),
            raw_hz=self.breadth.raw_hz(), prev30s_rate=rate, accel=accel,
            nh_counts={}, nl_counts={})
        last_ts = max((t.last_ts for t in self._tapes.values()), default=0)
        return EngineSnapshot(
            ts_ns=last_ts, breadth=breadth,
            top_movers=mk(top, lambda tp: tp.nh_count + tp.nl_count),
            new_highs=mk(highs, lambda tp: tp.nh_count),
            new_lows=mk(lows, lambda tp: tp.nl_count))

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
        for t in self._tapes.values():
            t.session = SessionExtreme()
            t.nh_count = 0
            t.nl_count = 0
        self._seen.clear()
