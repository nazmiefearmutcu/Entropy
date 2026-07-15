from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

_BLOCK = "█"


def bar(value: int, maxval: int, width: int) -> str:
    """Proportional block bar of `value` relative to `maxval`, capped at `width`."""
    if maxval <= 0 or value <= 0:
        return ""
    n = round(value / maxval * width)
    return _BLOCK * max(1, min(width, n))


class HighLowGauges(Widget):
    """Dual per-window Lows (red) vs Highs (green) bars, mirrored around a center.

    One line per rolling window:  ``<label>  <red lows◄| ▏ |►green highs>``.
    Bars are normalized to the largest count across both sides and all windows
    so the busiest window fills the available half-width.
    """

    nh_counts: reactive[dict[str, int]] = reactive(dict)
    nl_counts: reactive[dict[str, int]] = reactive(dict)
    window_labels: reactive[tuple[str, ...]] = reactive(("15m", "1h", "4h"))

    def watch_nh_counts(self, _o: dict[str, int], _n: dict[str, int]) -> None:
        self.refresh()

    def watch_nl_counts(self, _o: dict[str, int], _n: dict[str, int]) -> None:
        self.refresh()

    def watch_window_labels(self, _o: tuple[str, ...], _n: tuple[str, ...]) -> None:
        self.refresh()

    def render(self) -> Text:
        nh, nl = self.nh_counts, self.nl_counts
        peak = max([1, *nh.values(), *nl.values()])
        half = max(4, (self.size.width - 8) // 2)
        
        success = self.app.theme_variables.get("success", "#26d626")
        error = self.app.theme_variables.get("error", "#ff3b3b")
        
        out = Text(no_wrap=True)
        for wi, w in enumerate(self.window_labels):
            if wi:
                out.append("\n")
            lows = bar(nl.get(w, 0), peak, half)
            highs = bar(nh.get(w, 0), peak, half)
            out.append(f"{w:>3} ", style="#7a7a7a")
            out.append(f"{lows:>{half}}", style=error)  # right-aligned, grows leftward
            out.append("▏", style="#444444")
            out.append(f"{highs:<{half}}", style=success)  # left-aligned, grows rightward
        return out
