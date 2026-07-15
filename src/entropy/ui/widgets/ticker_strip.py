from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


def format_groups(groups: tuple[Any, ...], app: Any | None = None) -> Text:
    """Render per-window ticker groups as 'WIN: SYM n  SYM n | WIN: ...'."""
    if app is not None and hasattr(app, "theme_variables"):
        success = app.theme_variables.get("success", "#26d626")
        accent = app.theme_variables.get("accent", "#e6c200")
        primary = app.theme_variables.get("primary", "#39b0ff")
        secondary = app.theme_variables.get("secondary", "#ff7ab3")
        foreground = app.theme_variables.get("foreground", "#c8c8c8")
    else:
        success = "#26d626"
        accent = "#e6c200"
        primary = "#39b0ff"
        secondary = "#ff7ab3"
        foreground = "#c8c8c8"
    
    win_style = {"30s": success, "1m": accent, "5m": primary, "20m": secondary}

    out = Text(no_wrap=True, overflow="ellipsis")
    for gi, g in enumerate(groups):
        if gi:
            out.append("  ")
        out.append(f"{g.window}: ", style=f"bold {win_style.get(g.window, foreground)}")
        for sym, cnt in g.entries:
            out.append(f"{sym} ", style=foreground)
            out.append(f"{cnt} ", style="bold #ffffff")
    return out


class TickerStrip(Widget):
    """The '30s: GWW 15  APP 13 | 1m: ASML 18 ...' rolling-window activity strip."""

    groups: reactive[tuple[Any, ...]] = reactive(())

    def watch_groups(self, _old: tuple[Any, ...], _new: tuple[Any, ...]) -> None:
        self.refresh()

    def render(self) -> Text:
        return format_groups(self.groups, self.app)
