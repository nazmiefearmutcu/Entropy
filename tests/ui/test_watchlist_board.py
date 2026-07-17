import time

import msgspec
import pytest
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from entropy.app import AppConfig
from entropy.engine.leaderboard import LeaderRow
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.boards import refresh_board
from entropy.ui.widgets.watchlist_board import WatchlistBoard, sparkline

# --- sparkline helper ---------------------------------------------------------


def test_sparkline_empty():
    assert sparkline([]) == ""


def test_sparkline_constant():
    assert sparkline([5.0, 5.0, 5.0, 5.0]) == "▁▁▁▁"


def test_sparkline_ramp():
    assert sparkline([float(i) for i in range(8)]) == "▁▂▃▄▅▆▇█"


def test_sparkline_single_value():
    assert sparkline([42.0]) == "▁"


# --- board wiring ---------------------------------------------------------------


def _seed_watchlist(path, *symbols: str) -> None:
    payload = [
        {"symbol": s, "asset_class": "equity", "name": s, "venue": "us"} for s in symbols
    ]
    path.write_bytes(msgspec.json.encode(payload))


def _app(tmp_path) -> EntropyApp:
    return EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False,
        watchlist_path=str(tmp_path / "watchlist.json"),
    ))


@pytest.mark.asyncio
async def test_board_renders_quotes_and_sparkline(tmp_path):
    _seed_watchlist(tmp_path / "watchlist.json", "AAPL")
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        now = time.clock_gettime_ns(time.CLOCK_REALTIME)
        app.engine.on_trade("AAPL", 100.0, 1.0, "buy", now)
        app.engine.on_trade("AAPL", 101.0, 1.0, "buy", now + 1_000_000)
        app.sample_snapshot()
        board = app.query_one("#watchlist", WatchlistBoard)
        row = board.get_row("AAPL")
        assert row[0].plain == "AAPL"
        assert row[1].plain == "101.00"          # engine.quote last price
        assert row[2].plain.startswith("+")      # session Δ% is positive
        assert len(row[3].plain) == 1            # one snapshot -> one spark sample

        app.engine.on_trade("AAPL", 102.0, 1.0, "buy", now + 2_000_000)
        app.sample_snapshot()
        row = board.get_row("AAPL")
        assert row[1].plain == "102.00"          # Last updated from the new quote
        assert len(row[3].plain) == 2            # ring buffer grew by one sample
        await pilot.press("q")


@pytest.mark.asyncio
async def test_board_shows_placeholders_before_first_quote(tmp_path):
    _seed_watchlist(tmp_path / "watchlist.json", "TSLA")
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.sample_snapshot()
        board = app.query_one("#watchlist", WatchlistBoard)
        row = board.get_row("TSLA")
        assert row[1].plain == "—" and row[2].plain == "—" and row[3].plain == ""
        await pilot.press("q")


@pytest.mark.asyncio
async def test_watchlist_row_selection_sets_focus(tmp_path):
    _seed_watchlist(tmp_path / "watchlist.json", "AAPL")
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.sample_snapshot()
        board = app.query_one("#watchlist", WatchlistBoard)
        board.focus()
        await pilot.pause()
        await pilot.press("enter")               # row cursor select -> RowSelected
        assert app.focus_symbol == "AAPL"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_leaderboard_row_selection_sets_focus(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#new_lows", DataTable)
        assert table.cursor_type == "row"
        # Populate + capture the key synchronously (the 10 Hz snapshot timer
        # rebuilds these boards from the empty engine on every await).
        refresh_board(table, (LeaderRow("NVDA", 3, 88.0, -1.1),), app)
        key = table.coordinate_to_cell_key(Coordinate(0, 0)).row_key
        table.post_message(DataTable.RowSelected(table, 0, key))
        await pilot.pause()
        assert app.focus_symbol == "NVDA"
        await pilot.press("q")
