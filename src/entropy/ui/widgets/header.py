from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


class HeaderBar(Widget):
    clock = reactive("")
    quotes = reactive("")     # preformatted "SPY 750.42 (-0.02%) ..."
    sources = reactive("coinbase ●  binance ●")
    def watch_clock(self, *_: object) -> None: self.refresh()
    def watch_quotes(self, *_: object) -> None: self.refresh()
    def render(self) -> Text:
        accent = self.app.theme_variables.get("accent", "#e6c200")
        foreground = self.app.theme_variables.get("foreground", "#c8c8c8")
        success = self.app.theme_variables.get("success", "#26d626")
        
        t = Text()
        t.append("Entropy  ", style=f"bold {accent}")
        t.append(self.clock + "   ", style=foreground)
        t.append(self.sources + "\n", style=success)
        t.append(self.quotes, style=foreground)
        return t
