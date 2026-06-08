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
        t = Text()
        t.append("Entropy  ", style="bold #e6c200")
        t.append(self.clock + "   ", style="#c8c8c8")
        t.append(self.sources + "\n", style="#26d626")
        t.append(self.quotes, style="#c8c8c8")
        return t
