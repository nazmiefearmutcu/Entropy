# src/entropy/venues/clock.py
"""Venue clocks: market-hours gates for the multibot scheduler.

- :class:`CryptoClock` — always open, session label "24/7" (the crypto loop's
  current behavior, formalized).
- :class:`USEquitiesClock` — NASDAQ/NYSE regular trading hours (RTH) driven by
  the crocodile ``USMarketCalendar`` already installed in this venv (full
  observed US holiday set + 13:00 ET early-close half-days). Timezone-safe via
  ``ZoneInfo("America/New_York")``; NO external network calls.

crocodile is imported LAZILY (same discipline as ``feeds/equities/source.py``)
so a missing calendar dependency degrades to honest CLOSED instead of an
import error — but it is present in this environment.
"""
from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
_UTC = UTC

_MS_PER_S = 1000
# Extended-hours window labels (labels only; the venue never trades pre/post:
# Alpaca orders are sent with extended_hours=false).
_PRE_OPEN = time(4, 0)
_POST_CLOSE = time(20, 0)
_RTH_OPEN = time(9, 30)


def _ms_to_eastern(now_ms: int) -> datetime:
    return datetime.fromtimestamp(int(now_ms) / _MS_PER_S, tz=_UTC).astimezone(EASTERN)


class CryptoClock:
    """Crypto venue clock: 24/7, is_open always True."""

    label = "24/7"

    def is_open(self, now_ms: int) -> bool:
        return True

    def session_label(self, now_ms: int) -> str:
        return "24/7"


class USEquitiesClock:
    """US equities clock backed by the crocodile ``USMarketCalendar``.

    ``calendar`` needs ``is_market_open(dt)``, ``is_trading_day(date)`` and
    ``get_market_hours(date)`` (all satisfied by crocodile's USMarketCalendar,
    which models holidays AND 13:00 ET half-day closes); it is injectable so
    tests never depend on the wall clock. ``is_open``/``session_label`` take
    epoch MILLISECONDS (the runner's time currency).
    """

    label = "us-equities"

    def __init__(self, calendar: Any | None = None) -> None:
        self._calendar = calendar

    def _cal(self) -> Any:
        if self._calendar is None:
            from crocodile.core.scheduler.calendar import USMarketCalendar

            self._calendar = USMarketCalendar()
        return self._calendar

    def is_open(self, now_ms: int) -> bool:
        try:
            return bool(self._cal().is_market_open(_ms_to_eastern(now_ms)))
        except Exception:  # noqa: BLE001 — calendar bug = CLOSED, never a crash
            return False

    def session_label(self, now_ms: int) -> str:
        """'RTH' | 'PRE' | 'POST' | 'CLOSED' for the given epoch-ms instant.

        PRE = trading day, 04:00 ET (extended open) to 09:30; RTH = regular
        session (09:30 until the day's close — 13:00 on half-days); POST =
        after close until 20:00 ET; anything else (weekends, holidays, nights)
        = CLOSED.
        """
        try:
            cal = self._cal()
            dt = _ms_to_eastern(now_ms)
            d = dt.date()
            if not cal.is_trading_day(d):
                return "CLOSED"
            hours = cal.get_market_hours(d)
            if hours is None:
                return "CLOSED"
            open_dt, close_dt = hours
            if dt < open_dt:
                return "PRE" if _PRE_OPEN <= dt.time() < _RTH_OPEN else "CLOSED"
            if dt <= close_dt:
                return "RTH"
            return "POST" if dt.time() <= _POST_CLOSE else "CLOSED"
        except Exception:  # noqa: BLE001 — degrade honest, never crash the loop
            return "CLOSED"
