from __future__ import annotations

import logging
import os
from typing import Any

from rich.markup import escape
from textual.widgets import RichLog

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
        self._log_write_failed = False

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def _get_log_path(self) -> str | None:
        if getattr(self, "log_path", None) is not None:
            return self.log_path
        try:
            if (
                hasattr(self, "app")
                and self.app is not None
                and hasattr(self.app, "cfg")
                and self.app.cfg is not None
                and hasattr(self.app.cfg, "console_log_path")
            ):
                path: str | None = self.app.cfg.console_log_path
                return path
        except Exception:
            # self.app raises NoActiveAppError outside an app context; any cfg-shape
            # surprise just means "no mirror file", never a console crash.
            pass
        return None

    def _write_to_log_file(self, text: str) -> None:
        if self._log_write_failed:
            return
        path = self._get_log_path()
        if not path:
            return
        try:
            log_dir = os.path.dirname(path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception as exc:
            # One failure disables the file mirror for this widget's lifetime: retrying
            # every line would fail identically (bad path/permissions) and burn I/O on
            # the render path. The on-screen console keeps working; DEBUG because no
            # handlers are configured and stderr would corrupt the TUI.
            self._log_write_failed = True
            logging.getLogger(__name__).debug("console log mirror disabled: %s", exc)

    def push_event(self, e: StrategyEvent) -> None:
        text, color = render_event(e)
        self.write(f"[{color}]{escape(text)}[/]")
        self._write_to_log_file(text)

    def push_info(self, text: str, color: str = "white") -> None:
        self.write(f"[{color}]{escape(text)}[/]")
        self._write_to_log_file(text)
