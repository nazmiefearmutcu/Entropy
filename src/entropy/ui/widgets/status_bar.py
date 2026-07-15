# src/entropy/ui/widgets/status_bar.py
from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from .gauges import fill_cells

_ACCEL_LABEL = {
    "accelerating": "● Accelerating",
    "decelerating": "● Decelerating",
    "steady": "● Steady",
}
_BAR_HALF = 8   # cells per side of the sell/buy split bar


def format_telemetry(
    *, raw_hz: float, prev: float, snap_drops: int, spikes: int,
    accel: str, dropped: int
) -> str:
    drop = f"  dropped: {dropped}" if dropped else ""
    label = _ACCEL_LABEL.get(accel, accel)
    return (f"raw: {raw_hz:.0f} Hz   prev: {prev:.2f}/s   "
            f"snap-drops: {snap_drops}   spikes: {spikes}   {label}{drop}")


class StatusBar(Widget):
    sell_pct = reactive(50.0)
    telemetry = reactive("")
    hints = reactive("s:Settings  ?:Help  e:Errors  q:Quit")

    def watch_sell_pct(self, *_: object) -> None:
        self.refresh()

    def watch_telemetry(self, *_: object) -> None:
        self.refresh()

    def render(self) -> Text:
        sp = self.sell_pct
        bp = 100.0 - sp
        
        success = self.app.theme_variables.get("success", "#26d626")
        error = self.app.theme_variables.get("error", "#ff3b3b")
        foreground = self.app.theme_variables.get("foreground", "#c8c8c8")
        
        # dual split bar: red sell fill grows left, green buy fill grows right
        sell_bar = fill_cells(sp / 100.0, _BAR_HALF)[::-1]
        buy_bar = fill_cells(bp / 100.0, _BAR_HALF)
        t = Text()
        t.append(f"S {sp:.0f}% ", style=f"bold {error}")
        t.append(sell_bar, style=error)
        t.append("▏", style="#444444")
        t.append(buy_bar, style=success)
        t.append(f" B {bp:.0f}%   ", style=f"bold {success}")
        t.append(self.telemetry + "   ", style=foreground)
        t.append(self.hints, style="#7a7a7a")
        return t
