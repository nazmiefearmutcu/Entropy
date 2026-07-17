import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable

from entropy.engine.leaderboard import LeaderRow
from entropy.ui.widgets.boards import refresh_board, row_text


def test_row_text_colors_by_sign():
    cells = row_text(LeaderRow("AAPL", 5, 191.2, 2.4))
    assert cells[0].plain == "AAPL"
    assert cells[3].plain == "+2.40%"
    cells_dn = row_text(LeaderRow("NVDA", 3, 88.0, -1.1))
    assert cells_dn[3].plain == "-1.10%"


class _Harness(App[None]):
    def compose(self) -> ComposeResult:
        yield DataTable(id="t")


def _table(app: _Harness) -> DataTable:
    table = app.query_one("#t", DataTable)
    table.add_columns("Symbol", "Count", "Price", "%Chg")
    table.cursor_type = "row"
    return table


@pytest.mark.asyncio
async def test_refresh_board_same_symbols_updates_in_place_keeping_cursor():
    app = _Harness()
    async with app.run_test() as pilot:
        table = _table(app)
        refresh_board(table, (LeaderRow("AAPL", 1, 10.0, 0.5),
                              LeaderRow("NVDA", 2, 20.0, -0.2)))
        table.move_cursor(row=1)
        await pilot.pause()
        refresh_board(table, (LeaderRow("AAPL", 3, 11.0, 0.7),
                              LeaderRow("NVDA", 4, 21.0, -0.1)))
        assert table.cursor_coordinate.row == 1        # cursor survives the refresh
        assert table.get_row("NVDA")[1].plain == "4"   # cells updated in place
        assert table.get_row("AAPL")[2].plain == "11.00"


@pytest.mark.asyncio
async def test_refresh_board_membership_change_rebuilds():
    app = _Harness()
    async with app.run_test():
        table = _table(app)
        refresh_board(table, (LeaderRow("AAPL", 1, 10.0, 0.5),))
        refresh_board(table, (LeaderRow("TSLA", 9, 5.0, 0.1),))
        assert [key.value for key in table.rows] == ["TSLA"]
        assert table.get_row("TSLA")[1].plain == "9"


@pytest.mark.asyncio
async def test_refresh_board_order_change_rebuilds():
    app = _Harness()
    async with app.run_test():
        table = _table(app)
        refresh_board(table, (LeaderRow("AAPL", 1, 10.0, 0.5),
                              LeaderRow("NVDA", 2, 20.0, -0.2)))
        refresh_board(table, (LeaderRow("NVDA", 2, 20.0, -0.2),
                              LeaderRow("AAPL", 1, 10.0, 0.5)))
        assert [key.value for key in table.rows] == ["NVDA", "AAPL"]
