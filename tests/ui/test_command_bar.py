"""Command bar: pure parse_command grammar + pilot-driven execution wiring."""
from __future__ import annotations

import pytest

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.command_bar import (
    Command,
    CommandBar,
    CommandError,
    parse_command,
)
from entropy.ui.widgets.console import AlgoConsole
from entropy.ui.widgets.modals import HelpScreen

# --- parse_command grammar (pure) --------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("chart spy", Command(verb="chart", arg="SPY")),
    ("CHART SPY", Command(verb="chart", arg="SPY")),
    ("  chart   spy  ", Command(verb="chart", arg="SPY")),
    ("chart binance-spot:btcusdt", Command(verb="chart", arg="binance-spot:BTCUSDT")),
    ("chart COINBASE:eth-usd", Command(verb="chart", arg="coinbase:ETH-USD")),
    ("watch aapl", Command(verb="watch", arg="AAPL")),
    ("Watch AAPL", Command(verb="watch", arg="AAPL")),
    ("unwatch aapl", Command(verb="unwatch", arg="AAPL")),
    ("tf 1m", Command(verb="tf", arg="1m")),
    ("tf 5m", Command(verb="tf", arg="5m")),
    ("tf 15m", Command(verb="tf", arg="15m")),
    ("tf 1h", Command(verb="tf", arg="1h")),
    ("tf 4h", Command(verb="tf", arg="4h")),
    ("TF 1H", Command(verb="tf", arg="1h")),
    ("theme Nord", Command(verb="theme", arg="nord")),
    ("source sim", Command(verb="source", arg="sim")),
    ("source LIVE", Command(verb="source", arg="live")),
    ("source auto", Command(verb="source", arg="auto")),
    ("depth", Command(verb="depth", arg="")),                 # 0-arg toggle form
    ("depth aapl", Command(verb="depth", arg="AAPL")),        # 1-arg focus form
    ("DEPTH tsla", Command(verb="depth", arg="TSLA")),
    ("help", Command(verb="help", arg="")),
    ("HELP", Command(verb="help", arg="")),
])
def test_parse_valid(text: str, expected: Command) -> None:
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", [
    "",                 # empty
    "   ",              # whitespace only
    "frobnicate",       # unknown verb
    "chart",            # missing arg
    "chart A B",        # too many args
    "watch",
    "unwatch",
    "tf",
    "tf 2h",            # not a registered timeframe
    "tf 1h 5m",
    "theme",
    "source",
    "source real",      # not sim|live|auto
    "depth a b",        # depth takes at most one argument
    "depth a b c",
    "help me",          # help takes no argument
])
def test_parse_invalid(text: str) -> None:
    assert isinstance(parse_command(text), CommandError)


def test_depth_arity_message_allows_zero_or_one() -> None:
    # depth is the only 0-or-1 verb: its over-arity message must not claim
    # "exactly one argument" (zero — the toggle form — is valid too).
    err = parse_command("depth a b")
    assert isinstance(err, CommandError)
    assert "at most one argument" in err.message


def test_parse_error_messages_name_the_problem() -> None:
    err = parse_command("frobnicate")
    assert isinstance(err, CommandError) and "unknown command" in err.message
    err = parse_command("tf 2h")
    assert isinstance(err, CommandError) and "1m" in err.message  # lists choices
    err = parse_command("source real")
    assert isinstance(err, CommandError) and "sim" in err.message


# --- pilot wiring -------------------------------------------------------------

def _app(tmp_path) -> EntropyApp:
    return EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False,
        watchlist_path=str(tmp_path / "watchlist.json"),
    ))


def _console_text(app: EntropyApp) -> str:
    console = app.query_one("#console", AlgoConsole)
    return "\n".join(strip.text for strip in console.lines)


async def _submit(pilot, app: EntropyApp, text: str) -> CommandBar:
    """Open the bar with ':', type via value, submit with Enter."""
    bar = app.query_one("#cmdbar", CommandBar)
    if not bar.display:
        await pilot.press("colon")
        await pilot.pause()
    bar.value = text
    await pilot.press("enter")
    await pilot.pause()
    return bar


@pytest.mark.asyncio
async def test_colon_opens_escape_hides(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = app.query_one("#cmdbar", CommandBar)
        assert bar.display is False          # hidden by default
        await pilot.press("colon")
        await pilot.pause()
        assert bar.display is True
        assert app.focused is bar
        await pilot.press("escape")
        await pilot.pause()
        assert bar.display is False
        await pilot.press("q")


@pytest.mark.asyncio
async def test_chart_switches_focus_and_hides(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = await _submit(pilot, app, "chart spy")
        assert app.focus_symbol == "SPY"
        assert bar.display is False          # valid command closes the bar
        await pilot.press("q")


@pytest.mark.asyncio
async def test_watch_and_unwatch_drive_watchlist(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = await _submit(pilot, app, "watch aapl")
        assert "AAPL" in app._watchlist
        assert bar.display is False
        # watching again is a friendly no-op, still a valid command
        bar = await _submit(pilot, app, "watch AAPL")
        assert "AAPL" in app._watchlist
        assert "already watched" in _console_text(app)
        assert bar.display is False
        bar = await _submit(pilot, app, "unwatch aapl")
        assert "AAPL" not in app._watchlist
        assert bar.display is False
        await pilot.press("q")


@pytest.mark.asyncio
async def test_tf_triggers_timeframe_rebuild(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await _submit(pilot, app, "tf 1h")
        assert app.cfg.timeframe == "1h"
        assert app._candle_interval_ns == 3_600_000_000_000
        assert app.cfg.engine.window_labels == ("1h", "4h", "1d")
        assert app.engine.cfg.window_labels == ("1h", "4h", "1d")
        await pilot.press("q")


@pytest.mark.asyncio
async def test_theme_applies_and_persists_in_cfg(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = await _submit(pilot, app, "theme nord")
        assert app.theme == "nord"
        assert app.cfg.theme == "nord"
        assert bar.display is False
        await pilot.press("q")


@pytest.mark.asyncio
async def test_unknown_theme_errors_and_stays_open(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = await _submit(pilot, app, "theme not-a-theme")
        assert bar.display is True           # validation error keeps it open
        assert "unknown theme" in _console_text(app)
        assert app.cfg.theme == "entropy"    # nothing applied
        await pilot.press("escape")
        await pilot.press("q")


@pytest.mark.asyncio
async def test_source_hot_applies(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = await _submit(pilot, app, "source sim")
        assert app.cfg.equity_source == "sim"
        assert bar.display is False
        await pilot.press("q")


@pytest.mark.asyncio
async def test_help_pushes_help_screen(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = await _submit(pilot, app, "help")
        assert isinstance(app.screen, HelpScreen)
        assert bar.display is False
        await pilot.press("escape")
        await pilot.press("q")


@pytest.mark.asyncio
async def test_invalid_command_shows_error_and_stays_open(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        bar = await _submit(pilot, app, "frobnicate everything")
        assert bar.display is True
        assert "cmd:" in _console_text(app)
        assert "unknown command" in _console_text(app)
        # a corrected command still works from the open bar
        bar = await _submit(pilot, app, "chart nvda")
        assert app.focus_symbol == "NVDA"
        assert bar.display is False
        await pilot.press("q")
