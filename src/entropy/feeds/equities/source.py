# src/entropy/feeds/equities/source.py
"""Resolve the configured equity source ("sim" | "live" | "auto") to a feed kind.

"auto" picks "live" while the US market is open (Eastern time) and "sim"
otherwise. stockodile is imported LAZILY so the sim path never touches it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def _market_is_open(*, calendar: Any | None = None, now: datetime | None = None) -> bool:
    if calendar is None:
        # Lazy: only the "auto"/status paths need stockodile; "sim" never imports it.
        from stockodile.scheduler.calendar import USMarketCalendar
        calendar = USMarketCalendar()
    if now is None:
        now = datetime.now(EASTERN)
    return bool(calendar.is_market_open(now))


def resolve_equity_source(
    cfg_value: str,
    *,
    calendar: Any | None = None,
    now: datetime | None = None,
) -> str:
    """Map an AppConfig.equity_source value to a concrete "sim" or "live".

    ``calendar`` (needs ``.is_market_open(dt)``) and ``now`` (tz-aware) are
    injectable for tests; defaults are stockodile's USMarketCalendar and the
    current Eastern wall clock.
    """
    if cfg_value in ("sim", "live"):
        return cfg_value
    if cfg_value != "auto":
        raise ValueError(
            f"unknown equity_source {cfg_value!r} (expected 'sim', 'live' or 'auto')"
        )
    return "live" if _market_is_open(calendar=calendar, now=now) else "sim"


def market_status(*, calendar: Any | None = None, now: datetime | None = None) -> str:
    """"open"/"closed" per the US market calendar; "" if the answer is unavailable.

    Used by the header's NYSE chip from the app's periodic refresh timer, where
    Textual treats any uncaught exception as fatal — so a missing stockodile OR
    a calendar bug (external git dep) must degrade to a blank chip, not crash
    the TUI.
    """
    try:
        return "open" if _market_is_open(calendar=calendar, now=now) else "closed"
    except Exception:
        return ""
