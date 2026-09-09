"""PdtCounter math: rolling 5-TRADING-DAY window (holidays/weekends skip),
3-day-trade cap, persistence roundtrip, and window edge behavior.

Uses a STUB calendar with a fixed holiday so tests are date-independent and
zero-network (no crocodile wall-clock dependency).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from entropy.venues.alpaca_equities import PdtCounter

_ET = ZoneInfo("America/New_York")


class StubCalendar:
    """Trading days = weekdays except 2026-11-26 (Thanksgiving-like skip)."""

    HOLIDAYS = {date(2026, 11, 26)}  # Thursday holiday inside the tests

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.HOLIDAYS


def ms(y: int, mo: int, d: int, h: int = 10, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=_ET).timestamp() * 1000)


_CAL = StubCalendar()


def make_counter(tmp_path, calendar=_CAL) -> PdtCounter:
    return PdtCounter(tmp_path / "pdt.json", calendar=calendar)


# ---- cap math -----------------------------------------------------------------

def test_empty_counter_can_open(tmp_path):
    c = make_counter(tmp_path)
    assert c.can_open_trade(ms(2026, 9, 9)) is True
    assert c.count_in_window(ms(2026, 9, 9)) == 0


def test_three_fills_block_fourth(tmp_path):
    c = make_counter(tmp_path)
    day = ms(2026, 9, 9)
    for _ in range(3):
        assert c.can_open_trade(day) is True
        c.record_fill(day)
    assert c.count_in_window(day) == 3
    assert c.can_open_trade(day) is False


def test_record_fill_persists_across_instances(tmp_path):
    c1 = make_counter(tmp_path)
    c1.record_fill(ms(2026, 9, 9))
    c1.record_fill(ms(2026, 9, 8))
    c2 = make_counter(tmp_path)  # fresh instance, SAME file (supervisor-owned)
    assert c2.count_in_window(ms(2026, 9, 10)) == 2


def test_corrupt_file_starts_empty_not_crash(tmp_path):
    path = tmp_path / "pdt.json"
    path.write_text("{not json", encoding="utf-8")
    c = PdtCounter(path, calendar=StubCalendar())
    assert c.can_open_trade(ms(2026, 9, 9)) is True


# ---- rolling trading-day window ------------------------------------------------

def test_window_is_five_trading_days_rolling(tmp_path):
    c = make_counter(tmp_path)
    c.record_fill(ms(2026, 8, 31))  # Monday
    c.record_fill(ms(2026, 9, 1))   # Tuesday
    # Evaluated Wed 2026-09-09: window = Wed09,Tue08,Mon07,Fri04,Thu03.
    # Aug31/Sep1 fills are 7-8 calendar days back = OUTSIDE.
    assert c.count_in_window(ms(2026, 9, 9)) == 0
    c.record_fill(ms(2026, 9, 3))   # Thursday
    assert c.count_in_window(ms(2026, 9, 9)) == 1
    c.record_fill(ms(2026, 9, 8))   # Tuesday
    assert c.count_in_window(ms(2026, 9, 9)) == 2
    assert c.can_open_trade(ms(2026, 9, 9)) is True


def test_window_ages_out_after_five_trading_days(tmp_path):
    c = make_counter(tmp_path)
    c.record_fill(ms(2026, 9, 3))   # Thursday
    # Wed 2026-09-09 window = 09,08,07,04,03 -> Sep 3 is the 5th TD back: IN
    assert c.count_in_window(ms(2026, 9, 9)) == 1
    # Thu 2026-09-10 window = 10,09,08,07,04 -> Sep 3 just aged OUT
    assert c.count_in_window(ms(2026, 9, 10)) == 0
    assert c.can_open_trade(ms(2026, 9, 11)) is True


def test_window_skips_holidays(tmp_path):
    """Fill Tue 2026-11-24; evaluate Tue 2026-12-01. The window's 5 trading
    days = Dec1, Nov30, Nov27(Fri), Nov25(Wed), Nov24 — the Thanksgiving
    holiday (Nov 26) is skipped WITHOUT consuming a window slot, so the
    6-calendar-day-old Tuesday fill is still counted."""
    c = make_counter(tmp_path)
    c.record_fill(ms(2026, 11, 24))
    assert c.count_in_window(ms(2026, 12, 1)) == 1
    # One trading day later (Dec 2): window = Dec2,Dec1,Nov30,Nov27,Nov25
    # -> the Nov 24 fill has aged out.
    assert c.count_in_window(ms(2026, 12, 2)) == 0


def test_window_on_weekend_reaches_back_to_last_trading_days(tmp_path):
    c = make_counter(tmp_path)
    c.record_fill(ms(2026, 9, 4))   # Friday
    # Saturday 2026-09-05: window = Fri04,Thu03,Wed02,Tue01,Mon31
    assert c.count_in_window(ms(2026, 9, 5, 12, 0)) == 1
    assert c.count_in_window(ms(2026, 9, 6, 12, 0)) == 1  # Sunday too


def test_fills_older_than_cap_and_window_mixed(tmp_path):
    c = make_counter(tmp_path)
    c.record_fill(ms(2026, 9, 2))   # Wednesday (outside by Sep 9)
    c.record_fill(ms(2026, 9, 8))
    c.record_fill(ms(2026, 9, 9, 9, 45))
    assert c.count_in_window(ms(2026, 9, 9, 15, 0)) == 2
    assert c.can_open_trade(ms(2026, 9, 9, 15, 0)) is True
    c.record_fill(ms(2026, 9, 9, 15, 30))
    assert c.can_open_trade(ms(2026, 9, 9, 15, 45)) is False
