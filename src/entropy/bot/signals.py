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
    #: Per-bar RMS of returns (a fraction, e.g. 0.0011) at the entry bar, set by
    #: strategies on ENTRY signals. Sigma-scaled stop/TP barriers consume it;
    #: None (exits, strategies that do not measure it) falls back to percent.
    sigma: float | None = None
