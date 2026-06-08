from __future__ import annotations

from .engine import EventKind, StrategyEvent

_COLORS = {
    EventKind.OPEN_LONG: "green", EventKind.OPEN_SHORT: "red",
    EventKind.CLOSE_LONG: "yellow", EventKind.CLOSE_SHORT: "yellow",
    EventKind.INFO: "white",
}

def render_event(e: StrategyEvent) -> tuple[str, str]:
    if e.kind is EventKind.INFO:
        return (e.text or f"watching [{e.symbol}]", "white")
    side = "LONG" if e.kind in (EventKind.OPEN_LONG, EventKind.CLOSE_LONG) else "SHORT"
    if e.kind in (EventKind.OPEN_LONG, EventKind.OPEN_SHORT):
        return (f"OPEN {side} @ {e.price:.3f} running_pnl={e.running_pnl:.3f}", _COLORS[e.kind])
    return (f"CLOSE {side} @ {e.price:.3f} trade_pnl={e.trade_pnl:.3f}", _COLORS[e.kind])
