from __future__ import annotations

from typing import Any

from textual.widgets import RichLog

from entropy.strategy.engine import StrategyEvent
from entropy.strategy.format import render_event


class AlgoConsole(RichLog):
    def __init__(self, **kw: Any) -> None:
        kw.setdefault("markup", True)
        kw.setdefault("auto_scroll", True)
        kw.setdefault("max_lines", 2000)
        kw.setdefault("highlight", False)
        super().__init__(**kw)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def push_event(self, e: StrategyEvent) -> None:
        text, color = render_event(e)
        self.write(f"[{color}]{text}[/]")

    def push_info(self, text: str, color: str = "white") -> None:
        self.write(f"[{color}]{text}[/]")
