from __future__ import annotations

import os
from typing import Any

from textual.widgets import RichLog
from rich.markup import escape

from entropy.strategy.engine import StrategyEvent
from entropy.strategy.format import render_event


class AlgoConsole(RichLog):
    def __init__(self, log_path: str | None = None, **kw: Any) -> None:
        kw.setdefault("markup", True)
        kw.setdefault("auto_scroll", True)
        kw.setdefault("max_lines", 2000)
        kw.setdefault("highlight", False)
        super().__init__(**kw)
        self.log_path = log_path

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def _get_log_path(self) -> str | None:
        if getattr(self, "log_path", None) is not None:
            return self.log_path
        try:
            if hasattr(self, "app") and self.app is not None:
                if hasattr(self.app, "cfg") and self.app.cfg is not None:
                    if hasattr(self.app.cfg, "console_log_path"):
                        return self.app.cfg.console_log_path
        except (AttributeError, Exception):
            pass
        return None

    def _write_to_log_file(self, text: str) -> None:
        path = self._get_log_path()
        if not path:
            return
        try:
            log_dir = os.path.dirname(path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def push_event(self, e: StrategyEvent) -> None:
        text, color = render_event(e)
        self.write(f"[{color}]{escape(text)}[/]")
        self._write_to_log_file(text)

    def push_info(self, text: str, color: str = "white") -> None:
        self.write(f"[{color}]{escape(text)}[/]")
        self._write_to_log_file(text)
