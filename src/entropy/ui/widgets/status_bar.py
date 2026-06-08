# src/entropy/ui/widgets/status_bar.py
from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


def format_telemetry(
    *, raw_hz: float, prev30s: float, snap_drops: int, spikes: int,
    accel: str, dropped: int
) -> str:
    drop = f"  dropped: {dropped}" if dropped else ""
    return (f"raw: {raw_hz:.0f} Hz   prev30s: {prev30s:.2f}/s   "
            f"snap-drops: {snap_drops}   spikes: {spikes}   {accel}{drop}")


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
        t = Text()
        t.append(f"S {sp:.0f}% ", style="bold #ff3b3b")
        t.append(self.telemetry + "  ", style="#c8c8c8")
        t.append(f"B {100-sp:.0f}%   ", style="bold #26d626")
        t.append(self.hints, style="#7a7a7a")
        return t
