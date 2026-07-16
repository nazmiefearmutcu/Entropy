from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


class HeaderBar(Widget):
    clock = reactive("")
    quotes = reactive("")     # preformatted "SPY 750.42 (-0.02%) ..."
    sources = reactive("coinbase ●  binance ●")
    market_status = reactive("")  # "open" | "closed" | "" (chip hidden)
    def watch_clock(self, *_: object) -> None: self.refresh()
    def watch_quotes(self, *_: object) -> None: self.refresh()
    def watch_market_status(self, *_: object) -> None: self.refresh()
    def render(self) -> Text:
        accent = self.app.theme_variables.get("accent", "#e6c200")
        foreground = self.app.theme_variables.get("foreground", "#c8c8c8")
        success = self.app.theme_variables.get("success", "#26d626")
        error = self.app.theme_variables.get("error", "#d62b2b")

        t = Text()
        t.append("Entropy  ", style=f"bold {accent}")
        t.append(self.clock + "   ", style=foreground)
        t.append(self.sources, style=success)
        if self.market_status:
            open_ = self.market_status == "open"
            t.append(f"  NYSE {'OPEN' if open_ else 'CLOSED'}",
                     style=f"bold {success if open_ else error}")
        t.append("\n")
        t.append(self.quotes, style=foreground)
        return t
