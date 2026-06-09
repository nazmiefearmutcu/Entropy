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
