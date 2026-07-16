# src/entropy/engine/windows.py
from __future__ import annotations

from collections import deque


class MonotonicExtreme:
    """Rolling max (kind=+1) or min (kind=-1) over span_ns, O(1) amortized.
    step() queries the PRIOR extreme (before inserting) and reports a STRICT
    new extreme (> for max, < for min); equalling the extreme is not new.
    span_ns may be reassigned to hot-apply a new window: the buffer is kept and
    prunes lazily on the next evict()/step() (growing the span cannot resurrect
    already-evicted history)."""
    __slots__ = ("span_ns", "kind", "dq")

    def __init__(self, span_ns: int, kind: int) -> None:
        self.span_ns = span_ns
        self.kind = kind
        self.dq: deque[tuple[int, float]] = deque()

    def _dominates(self, a: float, b: float) -> bool:
        return a >= b if self.kind > 0 else a <= b

    def evict(self, now_ns: int) -> None:
        cutoff = now_ns - self.span_ns
        dq = self.dq
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def peek(self) -> float | None:
        return self.dq[0][1] if self.dq else None

    def step(self, ts_ns: int, price: float) -> bool:
        self.evict(ts_ns)
        prior = self.peek()
        is_new = prior is None or (price > prior if self.kind > 0 else price < prior)
        dq = self.dq
        while dq and self._dominates(price, dq[-1][1]):
            dq.pop()
        dq.append((ts_ns, price))
        return is_new


class SessionExtreme:
    """Cumulative session high/low + first price (for %Chg). O(1), 3 floats."""
    __slots__ = ("hi", "lo", "first_price")

    def __init__(self) -> None:
        self.hi: float | None = None
        self.lo: float | None = None
        self.first_price: float | None = None

    def step(self, price: float) -> tuple[bool, bool]:
        new_hi = self.hi is None or price > self.hi
        new_lo = self.lo is None or price < self.lo
        if new_hi:
            self.hi = price
        if new_lo:
            self.lo = price
        if self.first_price is None:
            self.first_price = price
        return new_hi, new_lo

    def pct_chg(self, price: float) -> float:
        if not self.first_price:
            return 0.0
        return (price - self.first_price) / self.first_price


class MomentumHorizon:
    """Maintains a (ts,price) deque; push() returns the reference price ~span
    ago (the newest anchor at or older than now-span)."""
    __slots__ = ("span_ns", "dq", "last_evicted")

    def __init__(self, span_ns: int) -> None:
        self.span_ns = span_ns
        self.dq: deque[tuple[int, float]] = deque()
        self.last_evicted: float | None = None

    def set_span(self, span_ns: int) -> None:
        """Hot-apply a new horizon, keeping the existing (ts, price) buffer.

        The buffer is pruned lazily by the next push() against the new span.
        Growing the span cannot resurrect already-evicted history, so the
        anchor may briefly be younger than the new horizon — the same warm-up
        contract as a fresh meter.
        """
        self.span_ns = span_ns

    def push(self, ts_ns: int, price: float) -> float:
        dq = self.dq
        dq.append((ts_ns, price))
        cutoff = ts_ns - self.span_ns
        while len(dq) >= 2 and dq[1][0] <= cutoff:
            self.last_evicted = dq.popleft()[1]
        return dq[0][1]

    def has_anchor(self, ts_ns: int) -> bool:
        """True once at least one tick older than the cutoff exists."""
        return bool(self.dq) and self.dq[0][0] <= ts_ns - self.span_ns
