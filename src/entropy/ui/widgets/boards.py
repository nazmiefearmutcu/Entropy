from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import DataTable

from entropy.engine.leaderboard import LeaderRow


def row_text(r: LeaderRow, app: Any | None = None) -> tuple[Text, Text, Text, Text]:
    if app is not None and hasattr(app, "theme_variables"):
        success = app.theme_variables.get("success", "#26d626")
        error = app.theme_variables.get("error", "#ff3b3b")
    else:
        success = "#26d626"
        error = "#ff3b3b"
    col = success if r.pct_chg >= 0 else error
    return (Text(r.symbol, style="bold"),
            Text(str(r.count), justify="right"),
            Text(f"{r.price:.2f}", justify="right"),
            Text(f"{r.pct_chg:+.2f}%", style=col, justify="right"))

def refresh_board(
    table: DataTable[object], rows: tuple[LeaderRow, ...], app: Any | None = None
) -> None:
    table.clear()
    for r in rows:
        table.add_row(*row_text(r, app))
