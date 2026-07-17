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
    table.clear()
    for r in rows:
        # Keyed by symbol so row selection can resolve it (app focus-symbol plumbing).
        table.add_row(*row_text(r, app), key=r.symbol)
