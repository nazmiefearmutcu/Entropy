from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import DataTable

from entropy.engine.leaderboard import LeaderRow


def board_colors(app: Any | None = None) -> tuple[str, str]:
    """(success, error) colors from the app theme, with static fallbacks."""
    if app is not None and hasattr(app, "theme_variables"):
        return (app.theme_variables.get("success", "#26d626"),
                app.theme_variables.get("error", "#ff3b3b"))
    return "#26d626", "#ff3b3b"


def row_text(r: LeaderRow, app: Any | None = None) -> tuple[Text, Text, Text, Text]:
    success, error = board_colors(app)
    col = success if r.pct_chg >= 0 else error
    return (Text(r.symbol, style="bold"),
            Text(str(r.count), justify="right"),
            Text(f"{r.price:.2f}", justify="right"),
            Text(f"{r.pct_chg:+.2f}%", style=col, justify="right"))

def refresh_board(
    table: DataTable[object], rows: tuple[LeaderRow, ...], app: Any | None = None
) -> None:
    """Refresh a leaderboard; rebuild only when the symbol set/order changed.

    Rows are keyed by symbol (row selection resolves the symbol for the app's
    focus plumbing), which also enables in-place cell updates while membership
    is unchanged — a clear()+rebuild at the 10 Hz snapshot rate would reset the
    keyboard row cursor on every tick.
    """
    symbols = [r.symbol for r in rows]
    if symbols != [key.value for key in table.rows]:
        table.clear()
        for r in rows:
            table.add_row(*row_text(r, app), key=r.symbol)
        return
    for r in rows:
        for column_key, cell in zip(table.columns, row_text(r, app), strict=True):
            table.update_cell(r.symbol, column_key, cell, update_width=True)
