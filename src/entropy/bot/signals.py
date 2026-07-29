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
    #: Optional risk hints. EXPOSURE is tightening-only; the STOP DISTANCE is not.
    #: `size_pct` becomes `min(profile.per_trade_pct, size_pct)`, so a strategy can
    #: never talk the risk layer into more exposure than the profile allows. The
    #: stop / take-profit distances are only bounded by the shared 50% ceiling and
    #: the 0% floor — NOT by the profile, which a hint REPLACES rather than
    #: tightens. A hint may therefore be wider than the profile's own stop: 50% is
    #: available against MEDIUM's 1.0% base (CV-scaled, so ~1.0017% on an ordinary
    #: window — the scaling moves it by a fraction of a percent, not by 50x). That
    #: gap is deliberate (a stop
    #: sized to the symbol's actual move is the whole point) but it means the
    #: per-trade LOSS a hint permits is not profile-bounded, only the notional is.
    #: All default to None, so every existing strategy is unaffected.
    size_pct: float | None = None
    stop_pct: float | None = None
    tp_pct: float | None = None
