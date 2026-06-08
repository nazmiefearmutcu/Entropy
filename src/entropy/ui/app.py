from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, RichLog, Static

from .theme import ENTROPY_THEME
from .widgets.charts import PriceChart, VolumeChart
from .widgets.gauges import GaugeBar
from .widgets.header import HeaderBar
from .widgets.status_bar import StatusBar


class EntropyApp(App[None]):
    CSS_PATH = "entropy.tcss"
    BINDINGS = [
        ("s", "settings", "Settings"),
        ("question_mark", "help", "Help"),
        ("h", "help", "Help"),
        ("e", "errors", "Errors"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, *args: Any, headless: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Horizontal(id="body"):
            yield RichLog(
                id="console", markup=True, highlight=False,
                auto_scroll=True, max_lines=2000,
            )
            with Vertical(id="center"):
                yield Static("", id="ticker")
                yield GaugeBar(id="gauges")
                yield Static("", id="hist")
                with Horizontal(id="boards"):
                    yield DataTable(id="new_lows")
                    yield DataTable(id="session_highs")
            with Vertical(id="charts"):
                yield PriceChart(id="price")
                yield VolumeChart(id="volume")
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        self.register_theme(ENTROPY_THEME)
        self.theme = "entropy"
        for tid in ("new_lows", "session_highs"):
            t = self.query_one("#" + tid, DataTable)
            t.add_columns("Symbol", "Count", "Price", "%Chg")
            t.cursor_type = "none"
            t.zebra_stripes = False

    def action_settings(self) -> None: ...
    def action_help(self) -> None: ...
    def action_errors(self) -> None: ...
