from __future__ import annotations

import enum

import msgspec


class WindowName(enum.StrEnum):
    W0 = "w0"
    W1 = "w1"
    W2 = "w2"
    SESSION = "session"


class EventKind(enum.StrEnum):
    NEW_HIGH = "new_high"
    NEW_LOW = "new_low"
    SPIKE = "spike"
    SNAP_DROP = "snap_drop"
    UPMOVE = "upmove"
    DOWNMOVE = "downmove"


class _Base(msgspec.Struct, frozen=True):
    symbol: str
    ts_ns: int
    price: float


class NewHigh(_Base, frozen=True):
    kind: EventKind = EventKind.NEW_HIGH
    window: WindowName = WindowName.SESSION
    prev_extreme: float | None = None


class NewLow(_Base, frozen=True):
    kind: EventKind = EventKind.NEW_LOW
    window: WindowName = WindowName.SESSION
    prev_extreme: float | None = None


class Spike(_Base, frozen=True):
    kind: EventKind = EventKind.SPIKE
    pct: float = 0.0
    horizon_s: float = 5.0
    ref_price: float = 0.0


class SnapDrop(_Base, frozen=True):
    kind: EventKind = EventKind.SNAP_DROP
    pct: float = 0.0
    horizon_s: float = 5.0
    ref_price: float = 0.0


class UpMove(_Base, frozen=True):
    kind: EventKind = EventKind.UPMOVE
    pct: float = 0.0
    horizon_s: float = 5.0
    ref_price: float = 0.0


class DownMove(_Base, frozen=True):
    kind: EventKind = EventKind.DOWNMOVE
    pct: float = 0.0
    horizon_s: float = 5.0
    ref_price: float = 0.0


Event = NewHigh | NewLow | Spike | SnapDrop | UpMove | DownMove
