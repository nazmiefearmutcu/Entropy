# tests/ui/test_equity_source.py
"""equity_source config: resolution, header NYSE chip, settings-driven feed swap."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from textual.widgets import Select

from entropy.app import AppConfig
from entropy.feeds.equities.source import market_status, resolve_equity_source
from entropy.feeds.equities.universe import LIVE_UNIVERSE
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.console import AlgoConsole
from entropy.ui.widgets.header import HeaderBar
from entropy.ui.widgets.modals import SettingsScreen

NOON = datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("America/New_York"))


class FakeCalendar:
    def __init__(self, is_open: bool) -> None:
        self._open = is_open
        self.seen: list[datetime] = []

    def is_market_open(self, dt: datetime) -> bool:
        self.seen.append(dt)
        return self._open


class FakePlan:
    provider_name = "stub_provider"
    trimmed_symbols: list[str] = []


def _make_live_stub(started_tasks: list[asyncio.Task], calls: list[tuple]):
    """A start_equity_feed stand-in: records the call, returns an idle task + plan."""

    async def stub(sink, symbols):
        calls.append((sink, tuple(symbols)))

        async def idle() -> None:
            await asyncio.sleep(3600)

        task = asyncio.get_running_loop().create_task(idle())
        started_tasks.append(task)
        return task, FakePlan()

    return stub


# --- resolve_equity_source unit matrix --------------------------------------

def test_auto_market_open_resolves_live():
    cal = FakeCalendar(True)
    assert resolve_equity_source("auto", calendar=cal, now=NOON) == "live"
    assert cal.seen == [NOON]


def test_auto_market_closed_resolves_sim():
    assert resolve_equity_source("auto", calendar=FakeCalendar(False), now=NOON) == "sim"


@pytest.mark.parametrize("explicit", ["sim", "live"])
def test_explicit_value_passes_through_without_calendar(explicit):
    cal = FakeCalendar(explicit != "live")  # opposite answer — must be ignored
    assert resolve_equity_source(explicit, calendar=cal, now=NOON) == explicit
    assert cal.seen == []


@pytest.mark.parametrize("bad", ["", "SIM", "Live", "real", "none"])
def test_unknown_value_raises(bad):
    with pytest.raises(ValueError):
        resolve_equity_source(bad, calendar=FakeCalendar(True), now=NOON)


def test_market_status_open_closed():
    assert market_status(calendar=FakeCalendar(True), now=NOON) == "open"
    assert market_status(calendar=FakeCalendar(False), now=NOON) == "closed"


def test_market_status_swallows_calendar_errors():
    """The chip is computed inside a Textual timer callback, where any uncaught
    exception kills the whole app — a broken calendar must yield a blank chip."""

    class BrokenCalendar:
        def is_market_open(self, dt: datetime) -> bool:
            raise RuntimeError("holiday table corrupt")

    assert market_status(calendar=BrokenCalendar(), now=NOON) == ""


# --- HeaderBar NYSE chip -----------------------------------------------------

@pytest.mark.asyncio
async def test_header_chip_renders_open_closed_and_blank():
    app = EntropyApp(AppConfig(enable_crypto=False, enable_equities=False))
    async with app.run_test(size=(120, 40)):
        header = app.query_one("#header", HeaderBar)
        header.market_status = "open"
        assert "NYSE OPEN" in str(header.render())
        header.market_status = "closed"
        rendered = str(header.render())
        assert "NYSE CLOSED" in rendered
        assert "NYSE OPEN" not in rendered
        header.market_status = ""
        assert "NYSE" not in str(header.render())


# --- boot behavior -----------------------------------------------------------

def _console_text(app: EntropyApp) -> str:
    console = app.query_one("#console", AlgoConsole)
    return "\n".join(strip.text for strip in console.lines)


@pytest.mark.asyncio
async def test_boot_with_sim_source_announces_and_runs_sim(monkeypatch):
    live_calls: list[tuple] = []
    monkeypatch.setattr(
        "entropy.ui.app.start_equity_feed", _make_live_stub([], live_calls)
    )
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="sim", equity_tps=10))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "equities: source=sim" in _console_text(app)
        assert live_calls == []  # sim boot never touches the live path


@pytest.mark.asyncio
async def test_boot_with_live_source_starts_live_feed(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        "entropy.ui.app.start_equity_feed", _make_live_stub([], calls)
    )
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="live"))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert len(calls) == 1
        assert calls[0][0] is app._sink
        assert calls[0][1] == tuple(LIVE_UNIVERSE)
        assert "equities: source=live (stub_provider)" in _console_text(app)


@pytest.mark.asyncio
async def test_live_startup_failure_falls_back_to_sim(monkeypatch):
    async def broken(sink, symbols):
        raise RuntimeError("no provider")

    monkeypatch.setattr("entropy.ui.app.start_equity_feed", broken)
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="live", equity_tps=10))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        text = _console_text(app)
        assert "equities: live feed failed (no provider); falling back to sim" in text
        assert "equities: source=sim" in text


# --- settings-driven source swap ----------------------------------------------

@pytest.mark.asyncio
async def test_settings_shows_equity_source_row():
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="sim"))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        assert screen.query_one("#set-equity-source", Select).value == "sim"


@pytest.mark.asyncio
async def test_settings_switch_sim_to_live_rebuilds_feed(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        "entropy.ui.app.start_equity_feed", _make_live_stub([], calls)
    )
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="sim", equity_tps=10))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        app.screen.query_one("#set-equity-source", Select).value = "live"
        await pilot.click("#btn-save")
        await pilot.pause()
        assert app.cfg.equity_source == "live"
        assert len(calls) == 1
        assert calls[0][1] == tuple(LIVE_UNIVERSE)


@pytest.mark.asyncio
async def test_settings_switch_live_to_sim_cancels_live_task(monkeypatch):
    started: list[asyncio.Task] = []
    monkeypatch.setattr(
        "entropy.ui.app.start_equity_feed", _make_live_stub(started, [])
    )
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="live", equity_tps=10))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        assert len(started) == 1
        await pilot.press("s")
        app.screen.query_one("#set-equity-source", Select).value = "sim"
        await pilot.click("#btn-save")
        for _ in range(20):  # exclusive-worker cancel propagates asynchronously
            if started[0].cancelled():
                break
            await pilot.pause()
        assert app.cfg.equity_source == "sim"
        # sim + live must never run together: the old collect task was cancelled.
        assert started[0].cancelled()
        assert "equities: source=sim" in _console_text(app)


@pytest.mark.asyncio
async def test_settings_unchanged_source_does_not_restart_feed(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        "entropy.ui.app.start_equity_feed", _make_live_stub([], calls)
    )
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="live", equity_tps=10))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        assert len(calls) == 1
        await pilot.press("s")
        await pilot.click("#btn-save")  # save with no changes
        await pilot.pause()
        assert len(calls) == 1  # no spurious feed restart
