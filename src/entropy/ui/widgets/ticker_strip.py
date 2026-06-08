from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

# Window label colors, cycling so each group is visually distinct.
_WIN_STYLE = {"30s": "#26d626", "1m": "#e6c200", "5m": "#39b0ff", "20m": "#ff7ab3"}


def format_groups(groups: tuple[Any, ...]) -> Text:
    """Render per-window ticker groups as 'WIN: SYM n  SYM n | WIN: ...'.

    `groups` is an EngineSnapshot.ticker tuple of objects with `.window` (str)
    and `.entries` (tuple of (symbol, count)).
    """
    out = Text(no_wrap=True, overflow="ellipsis")
    for gi, g in enumerate(groups):
        if gi:
            out.append("  ")
        out.append(f"{g.window}: ", style=f"bold {_WIN_STYLE.get(g.window, '#c8c8c8')}")
        for sym, cnt in g.entries:
            out.append(f"{sym} ", style="#c8c8c8")
            out.append(f"{cnt} ", style="bold #ffffff")
    return out


class TickerStrip(Widget):
    """The '30s: GWW 15  APP 13 | 1m: ASML 18 ...' rolling-window activity strip."""

    groups: reactive[tuple[Any, ...]] = reactive(())

    def watch_groups(self, _old: tuple[Any, ...], _new: tuple[Any, ...]) -> None:
        self.refresh()

    def render(self) -> Text:
        return format_groups(self.groups)
