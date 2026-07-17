from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass

from textual.reactive import reactive
from textual_plotext import PlotextPlot

_NS_PER_HOUR = 3_600 * 1_000_000_000
_NS_PER_DAY = 24 * _NS_PER_HOUR

# Second-scale bars: default for charts whose owner never sets a timeframe
# (bare widgets in tests / legacy call sites).
_LEGACY_BAR_NS = 1_000_000_000


# A chart deque holds up to this many bars (CandleAggregator's maxlen), so the
# rendered span is bar_ns * _CHART_BARS — the tier must key on THAT, not bar_ns:
# 15m bars look sub-hour but 120 of them span 30h, wrapping "H:M" past midnight.
_CHART_BARS = 120


def _axis_formats(bar_ns: int) -> tuple[str, str]:
    """Map a bar interval to ``(plotext date_form, strftime format)`` for the x-axis.

    plotext's ``date_form()`` takes strftime letters WITHOUT the ``%`` — it inserts
    one before every alphabetic character (``plotext._date.date_class.correct_form``)
    — so the two returned strings must stay letter-for-letter in sync.
    """
    if bar_ns >= _NS_PER_DAY:
        # Day-scale and coarser bars: date only.
        return "d/m/Y", "%d/%m/%Y"
    if bar_ns * _CHART_BARS > _NS_PER_DAY:
        # The full chart span crosses a midnight (15m/1h/4h at 120 bars):
        # prefix the day so labels stay unique for the chart's lifetime.
        return "d/m H:M", "%d/%m %H:%M"
    # Chart span fits within a day (legacy 1s, 1m/5m): HH:MM is unique.
    return "H:M", "%H:%M"


@dataclass(slots=True)
class Candle:
    t: int          # ns
    o: float
    h: float
    l: float  # noqa: E741
    c: float


def align_overlay(n_candles: int, values: Sequence[float]) -> list[float | None]:
    """Right-align an overlay series against ``n_candles`` x-slots.

    Indicator series (EMA & co) may be SHORTER than the candle list (warmup):
    pad the FRONT with ``None`` so the last overlay value sits on the last
    candle. A series longer than the chart keeps only its most recent
    ``n_candles`` values. plotext can't plot ``None`` — ``replot()`` slices
    the x-range past the padding instead of plotting it.
    """
    if n_candles <= 0:
        return []
    tail = [float(v) for v in values[-n_candles:]]
    padded: list[float | None] = [None] * (n_candles - len(tail))
    padded.extend(tail)
    return padded


def split_volume(
    vols: Sequence[float], ups: Sequence[bool]
) -> tuple[list[float], list[float]]:
    """Split volumes into (up, down) series, zero-filling the opposite slots.

    Two same-x ``plt.bar`` calls then render as one green/red bar row: the
    zero-height bars of the other series draw nothing at that x position.
    """
    up = [v if u else 0.0 for v, u in zip(vols, ups, strict=True)]
    down = [0.0 if u else v for v, u in zip(vols, ups, strict=True)]
    return up, down


class ChartRedrawMemo:
    """Skip-redraw memo for the 10 Hz snapshot fan-out.

    The app fingerprints everything a chart pair renders from — bar count,
    last close/volume, chart type, volume toggle, theme, symbol, timeframe —
    and skips the full plotext clear+rebuild while the fingerprint is
    unchanged. Pure bookkeeping: no widget or app references.
    """

    def __init__(self) -> None:
        self._keys: dict[str, tuple[object, ...]] = {}

    def is_stale(self, chart_id: str, key: tuple[object, ...]) -> bool:
        """True when ``key`` differs from the last recorded draw of ``chart_id``."""
        return self._keys.get(chart_id) != key

    def record(self, chart_id: str, key: tuple[object, ...]) -> None:
        self._keys[chart_id] = key


def _hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    """``#rrggbb`` → RGB tuple (plotext takes tuples/names, not hex strings)."""
    s = value.strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def _theme_rgb(widget: PlotextPlot, var: str, fallback_hex: str) -> tuple[int, int, int]:
    """Resolve a theme variable to an RGB tuple, falling back to a literal hex.

    Mirrors the ``app.theme_variables`` pattern used across widgets; the app
    lookup is best-effort so bare widgets (unit tests) still render.
    """
    raw = fallback_hex
    with suppress(Exception):  # no active app / no theme_variables on it
        raw = widget.app.theme_variables.get(var) or fallback_hex
    return _hex_to_rgb(raw) or _hex_to_rgb(fallback_hex) or (200, 200, 200)


# Overlay line palette: (theme variable, fallback hex), cycled per series.
# Distinct from the candlestick's own green/red so EMA lines stay readable.
_OVERLAY_PALETTE = (
    ("accent", "#e6c200"),
    ("primary", "#39b0ff"),
    ("warning", "#f0c040"),
)
_VOL_UP = ("success", "#26d626")
_VOL_DOWN = ("error", "#ff3b3b")


class PriceChart(PlotextPlot):
    # always_update so a same-length list reassignment each frame still repaints.
    candles: reactive[list[Candle]] = reactive(list, always_update=True)
    chart_type: reactive[str] = reactive("candlestick")
    # Bar interval driving the x-axis label format; the app keeps this in sync
    # with the active TimeframeSpec.bar_ns.
    bar_ns: int = _LEGACY_BAR_NS
    # Chart heading ("SYMBOL · timeframe"); the app keeps this in sync with the
    # focus/strategy symbol and the active timeframe. Empty renders no title.
    title: str = ""
    # Named overlay lines (e.g. {"EMA9": [...]}) drawn over the price plot;
    # shorter-than-candles series are right-aligned (see align_overlay). Set
    # via set_series() so one reactive assignment triggers a single replot.
    overlays: dict[str, list[float]] | None = None

    def set_series(
        self, candles: list[Candle], overlays: dict[str, list[float]] | None = None
    ) -> None:
        """Replace candles + overlays in one shot.

        Overlays land on a plain attribute first; the reactive ``candles``
        assignment then triggers the single replot that draws both.
        """
        self.overlays = dict(overlays) if overlays else None
        self.candles = candles

    def watch_candles(self, _old: list[Candle], new: list[Candle]) -> None:
        if new:
            self.replot()

    def watch_chart_type(self, _old: str, new: str) -> None:
        self.replot()

    def replot(self) -> None:
        self.plt.clear_data()
        if self.title:
            self.plt.title(self.title)
        date_form, fmt = _axis_formats(self.bar_ns)
        self.plt.date_form(date_form)
        ds = [dt.datetime.fromtimestamp(c.t / 1e9).strftime(fmt) for c in self.candles]
        data = {"Open": [c.o for c in self.candles], "Close": [c.c for c in self.candles],
                "High": [c.h for c in self.candles], "Low": [c.l for c in self.candles]}

        if self.chart_type == "line":
            self.plt.plot(ds, data["Close"])
        else:
            self.plt.candlestick(ds, data)
        self._plot_overlays(ds)
        self.refresh()

    def _plot_overlays(self, ds: list[str]) -> None:
        """Draw each named overlay as a themed line over the price plot.

        align_overlay() front-pads short series with None; plotext can't plot
        None, so the line is plotted against the x-slice past the padding.
        """
        n = len(self.candles)
        for i, (name, values) in enumerate((self.overlays or {}).items()):
            aligned = align_overlay(n, values)
            start = next((j for j, v in enumerate(aligned) if v is not None), n)
            ys = [v for v in aligned[start:] if v is not None]
            if not ys:
                continue
            var, fallback = _OVERLAY_PALETTE[i % len(_OVERLAY_PALETTE)]
            self.plt.plot(
                ds[start:], ys, color=_theme_rgb(self, var, fallback), label=name
            )

class VolumeChart(PlotextPlot):
    bars: reactive[list[tuple[int, float]]] = reactive(list, always_update=True)
    bar_ns: int = _LEGACY_BAR_NS
    # Per-bar up/down flags (close >= open) coloring the bars green/red; None
    # keeps the legacy single-series look (bare `bars = ...` callers, or
    # candle data without opens). Set via set_series().
    ups: list[bool] | None = None

    def set_series(
        self, bars: list[tuple[int, float]], ups: list[bool] | None = None
    ) -> None:
        """Replace bars + up/down flags in one shot (single replot).

        A length-mismatched ``ups`` is dropped rather than trusted: the split
        would raise mid-replot inside a reactive watcher and take the TUI down.
        """
        self.ups = list(ups) if ups is not None and len(ups) == len(bars) else None
        self.bars = bars

    def watch_bars(self, _old: list[tuple[int, float]], new: list[tuple[int, float]]) -> None:
        if new:
            self.replot()
    def replot(self) -> None:
        self.plt.clear_data()
        date_form, fmt = _axis_formats(self.bar_ns)
        self.plt.date_form(date_form)
        ds = [dt.datetime.fromtimestamp(t / 1e9).strftime(fmt) for t, _ in self.bars]
        vols = [v for _, v in self.bars]
        if self.ups is not None and len(self.ups) == len(vols):
            up, down = split_volume(vols, self.ups)
            self.plt.bar(ds, up, color=_theme_rgb(self, *_VOL_UP))
            self.plt.bar(ds, down, color=_theme_rgb(self, *_VOL_DOWN))
        else:
            self.plt.bar(ds, vols)
        self.refresh()
