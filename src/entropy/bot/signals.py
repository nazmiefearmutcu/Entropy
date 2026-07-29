from __future__ import annotations

import enum

import msgspec


class SignalAction(enum.StrEnum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT = "exit"


class Signal(msgspec.Struct, frozen=True):
    symbol: str
    action: SignalAction
    strength: float  # 0.0–1.0 confidence
    reason: str
    ts_ns: int
    strategy: str
    #: Optional risk hints. The risk manager applies them as TIGHTENING ONLY:
    #: `size_pct` becomes `min(profile.per_trade_pct, size_pct)`, and the stop /
    #: take-profit distances are clamped by the same ceiling the profile is. A
    #: strategy that knows how far this symbol usually moves can therefore ask
    #: for a stop that fits the move instead of a fixed percentage — but it can
    #: never talk the risk layer into more exposure than the profile allows.
    #: All default to None, so every existing strategy is unaffected.
    size_pct: float | None = None
    stop_pct: float | None = None
    tp_pct: float | None = None
