"""Watchlist board: persistent symbols with live quote and a unicode sparkline.

The app refreshes it from ``sample_snapshot``: engine quotes fill Last/Δ%, and a
per-symbol ring buffer of sampled last prices renders as block glyphs. Rows are
keyed by symbol, so a row selection bubbles to the app as ``DataTable.RowSelected``
and sets ``focus_symbol``. Cell updates happen in place while the watched set is
unchanged, keeping the row cursor stable across the 10 Hz refresh.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import msgspec
from rich.text import Text
from textual.widgets import DataTable
from textual.widgets.data_table import ColumnKey

from .boards import board_colors

# Ring-buffer length backing each row's sparkline (one sample per snapshot).
SPARK_WINDOW = 30

_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: Sequence[float]) -> str:
    """Render ``values`` as unicode block glyphs, one char per sample.

    Empty input -> ""; constant (or non-finite-span) input -> a flat baseline.
    """
    if not values:
        return ""
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return _BLOCKS[0] * len(values)
    lo, hi = min(finite), max(finite)
    span = hi - lo
    if not (span > 0.0 and math.isfinite(span)):
        return _BLOCKS[0] * len(values)
    top = len(_BLOCKS) - 1
    out: list[str] = []
    for v in values:
        if not math.isfinite(v):
            out.append(_BLOCKS[0])
            continue
        idx = round((v - lo) / span * top)
        out.append(_BLOCKS[min(max(idx, 0), top)])
    return "".join(out)


class WatchRow(msgspec.Struct, frozen=True):
    """One rendered watchlist row; quote fields stay None until the engine
    has seen a trade for the symbol."""

    symbol: str
    last: float | None
    pct: float | None
    spark: str


def _cells(row: WatchRow, app: Any | None) -> tuple[Text, Text, Text, Text]:
    success, error = board_colors(app)
    last = Text(f"{row.last:.2f}" if row.last is not None else "—", justify="right")
    if row.pct is None:
        pct = Text("—", justify="right")
    else:
        pct = Text(f"{row.pct:+.2f}%", style=success if row.pct >= 0 else error,
                   justify="right")
    return (Text(row.symbol, style="bold"), last, pct, Text(row.spark))


class WatchlistBoard(DataTable[Text]):
    """``Symbol | Last | Δ% | Spark`` table over the persistent watchlist."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._symbols: list[str] = []
        self._col_keys: list[ColumnKey] = []

    def on_mount(self) -> None:
        self._col_keys = list(self.add_columns("Symbol", "Last", "Δ%", "Spark"))
        self.cursor_type = "row"
        self.zebra_stripes = False

    def update_rows(self, rows: Sequence[WatchRow], app: Any | None = None) -> None:
        """Refresh the table; rebuild only when the watched set/order changed."""
        symbols = [r.symbol for r in rows]
        if symbols != self._symbols:
            self.clear()
            for r in rows:
                self.add_row(*_cells(r, app), key=r.symbol)
            self._symbols = symbols
            return
        for r in rows:
            for key, cell in zip(self._col_keys, _cells(r, app), strict=True):
                self.update_cell(r.symbol, key, cell, update_width=True)
