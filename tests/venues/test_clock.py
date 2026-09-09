"""Venue clocks: CryptoClock 24/7 + USEquitiesClock against the REAL crocodile
USMarketCalendar (holidays + 13:00 ET half-days) — offline, fixed instants.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from entropy.venues.clock import CryptoClock, USEquitiesClock

_ET = ZoneInfo("America/New_York")


def ms(y: int, mo: int, d: int, h: int = 12, mi: int = 0, s: int = 0) -> int:
    """Epoch-ms for an America/New_York wall instant (DST-safe via ZoneInfo)."""
    return int(datetime(y, mo, d, h, mi, s, tzinfo=_ET).timestamp() * 1000)


# ---- CryptoClock -----------------------------------------------------------

def test_crypto_clock_always_open():
    c = CryptoClock()
    for now in (0, ms(2026, 9, 9, 9, 30), ms(2026, 12, 25, 12, 0)):
        assert c.is_open(now) is True
        assert c.session_label(now) == "24/7"


# ---- USEquitiesClock: regular Monday ---------------------------------------

def test_rth_open_and_close_boundaries_regular_day():
    cal = USEquitiesClock()
    day = (2026, 9, 9)  # Wednesday, regular session
    assert cal.session_label(ms(*day, 9, 29, 59)) == "PRE"
    assert cal.session_label(ms(*day, 9, 30, 0)) == "RTH"    # 09:30 open
    assert cal.is_open(ms(*day, 9, 30, 0)) is True
    assert cal.is_open(ms(*day, 12, 0)) is True
    assert cal.session_label(ms(*day, 15, 59)) == "RTH"
    assert cal.is_open(ms(*day, 16, 0, 0)) is True   # calendar close is inclusive
    assert cal.is_open(ms(*day, 16, 0, 1)) is False
    assert cal.session_label(ms(*day, 16, 30)) == "POST"
    assert cal.session_label(ms(*day, 20, 0)) == "POST"
    assert cal.session_label(ms(*day, 20, 0, 1)) == "CLOSED"
    assert cal.session_label(ms(*day, 3, 59)) == "CLOSED"   # before 04:00 ET
    assert cal.session_label(ms(*day, 4, 0)) == "PRE"


# ---- weekends / holidays ------------------------------------------------------

def test_weekend_closed():
    cal = USEquitiesClock()
    sat, sun = (2026, 9, 5), (2026, 9, 6)
    for d in (sat, sun):
        assert cal.is_open(ms(*d, 12, 0)) is False
        assert cal.session_label(ms(*d, 12, 0)) == "CLOSED"


def test_holidays_closed():
    cal = USEquitiesClock()
    for d in ((2026, 9, 7),      # Labor Day (1st Monday Sep)
              (2026, 11, 26),    # Thanksgiving
              (2026, 12, 25),    # Christmas
              (2026, 4, 3)):     # Good Friday
        assert cal.is_open(ms(*d, 12, 0)) is False, d
        assert cal.session_label(ms(*d, 12, 0)) == "CLOSED", d


# ---- half-days (13:00 ET close) ---------------------------------------------

def test_half_day_closes_at_13_00():
    cal = USEquitiesClock()
    bf, xmas_eve = (2026, 11, 27), (2026, 12, 24)  # Black Friday, Christmas Eve
    for d in (bf, xmas_eve):
        assert cal.session_label(ms(*d, 12, 59)) == "RTH"
        assert cal.is_open(ms(*d, 12, 59, 59)) is True
        assert cal.is_open(ms(*d, 13, 0, 0)) is True    # calendar close inclusive
        assert cal.is_open(ms(*d, 13, 0, 1)) is False   # half-day: 13:00:01 shut
        assert cal.session_label(ms(*d, 13, 30)) == "POST"


def test_ms_input_is_utc_epoch_not_wall_clock():
    """Guard the timezone contract: a UTC-noon epoch is 08:00 ET (CLOSED-pre),
    and an ET-09:30 epoch is a different UTC instant."""
    cal = USEquitiesClock()
    utc_noon = int(datetime(2026, 9, 9, 12, 0,
                            tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    assert cal.session_label(utc_noon) == "PRE"  # 08:00 ET
    assert ms(2026, 9, 9, 9, 30) != utc_noon
