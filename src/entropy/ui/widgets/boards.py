from __future__ import annotations

from rich.text import Text
from textual.widgets import DataTable

from entropy.engine.leaderboard import LeaderRow


def row_text(r: LeaderRow) -> tuple[Text, Text, Text, Text]:
    col = "#26d626" if r.pct_chg >= 0 else "#ff3b3b"
    return (Text(r.symbol, style="bold"),
            Text(str(r.count), justify="right"),
            Text(f"{r.price:.2f}", justify="right"),
            Text(f"{r.pct_chg:+.2f}%", style=col, justify="right"))

def refresh_board(table: "DataTable[object]", rows: tuple[LeaderRow, ...]) -> None:
    table.clear()
    for r in rows:
        table.add_row(*row_text(r))
