from __future__ import annotations

from collections import deque

from rich.segment import Segment
from rich.style import Style
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

_EIGHTHS = " ▏▎▍▌▋▊▉█"


def fill(value: float, width: int) -> str:
    """Proportional sub-cell fill (0..1) across `width` cells using 1/8 blocks."""
    value = max(0.0, min(1.0, value))
    filled = value * width
    full = int(filled)
    rem = int((filled - full) * 8)
    s = "█" * full + (_EIGHTHS[rem] if rem and full < width else "")
    return s.ljust(width)[:width]


class EventHistogram(Widget):
    """Rolling event-rate bar: current raw_hz normalized to a short rolling peak.

    Bright-yellow when busy (>75% of recent peak), dim-green when quiet — gives
    the at-a-glance "tape speed" the original showed next to the rate counters.
    """

    raw_hz: reactive[float] = reactive(0.0)

    def __init__(
        self,
        *,
        history: int = 60,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._history: deque[float] = deque(maxlen=history)

    def watch_raw_hz(self, value: float) -> None:
        self._history.append(value)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        if not self._history:
            return Strip([Segment(" " * w)], w)
        peak = max(self._history) or 1.0
        cur = self._history[-1]
        ratio = cur / peak
        label = f" {cur:>6.0f} Hz"
        bar = fill(ratio, max(0, w - len(label)))
        color = "#f0c040" if ratio > 0.75 else "#26d626"
        return Strip([Segment(bar, Style(color=color)),
                      Segment(label, Style(color="#7a7a7a"))], w)
