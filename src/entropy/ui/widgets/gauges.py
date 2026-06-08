from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

_EIGHTHS = " ▏▎▍▌▋▊▉█"  # 0..8/8

def fill_cells(value: float, width: int) -> str:
    value = max(0.0, min(1.0, value))
    filled = value * width
    full = int(filled)
    rem = int((filled - full) * 8)
    s = "█" * full + (_EIGHTHS[rem] if rem and full < width else "")
    return s.ljust(width)[:width]

class GaugeBar(Widget):
    value = reactive(0.0)
    color = reactive("#26d626")
    def watch_value(self, *_: object) -> None: self.refresh()
    def render_line(self, y: int) -> Strip:
        w = self.size.width
        s = fill_cells(self.value, w)
        return Strip([Segment(s, Style(color=self.color))], w)
