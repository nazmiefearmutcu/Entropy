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
    *, raw_hz: float, prev30s: float, snap_drops: int, spikes: int,
    accel: str, dropped: int
) -> str:
    drop = f"  dropped: {dropped}" if dropped else ""
    label = _ACCEL_LABEL.get(accel, accel)
    return (f"raw: {raw_hz:.0f} Hz   prev30s: {prev30s:.2f}/s   "
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
        # dual split bar: red sell fill grows left, green buy fill grows right
        sell_bar = fill_cells(sp / 100.0, _BAR_HALF)[::-1]
        buy_bar = fill_cells(bp / 100.0, _BAR_HALF)
        t = Text()
        t.append(f"S {sp:.0f}% ", style="bold #ff3b3b")
        t.append(sell_bar, style="#ff3b3b")
        t.append("▏", style="#444444")
        t.append(buy_bar, style="#26d626")
        t.append(f" B {bp:.0f}%   ", style="bold #26d626")
        t.append(self.telemetry + "   ", style="#c8c8c8")
        t.append(self.hints, style="#7a7a7a")
        return t
