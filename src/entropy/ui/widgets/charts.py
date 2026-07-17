from __future__ import annotations

import datetime as dt
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

class PriceChart(PlotextPlot):
    # always_update so a same-length list reassignment each frame still repaints.
    candles: reactive[list[Candle]] = reactive(list, always_update=True)
    chart_type: reactive[str] = reactive("candlestick")
    # Bar interval driving the x-axis label format; the app keeps this in sync
    # with the active TimeframeSpec.bar_ns.
    bar_ns: int = _LEGACY_BAR_NS

    def watch_candles(self, _old: list[Candle], new: list[Candle]) -> None:
        if new:
            self.replot()

    def watch_chart_type(self, _old: str, new: str) -> None:
        self.replot()

    def replot(self) -> None:
        self.plt.clear_data()
        date_form, fmt = _axis_formats(self.bar_ns)
        self.plt.date_form(date_form)
        ds = [dt.datetime.fromtimestamp(c.t / 1e9).strftime(fmt) for c in self.candles]
        data = {"Open": [c.o for c in self.candles], "Close": [c.c for c in self.candles],
                "High": [c.h for c in self.candles], "Low": [c.l for c in self.candles]}

        if self.chart_type == "line":
            self.plt.plot(ds, data["Close"])
        else:
            self.plt.candlestick(ds, data)
        self.refresh()

class VolumeChart(PlotextPlot):
    bars: reactive[list[tuple[int, float]]] = reactive(list, always_update=True)
    bar_ns: int = _LEGACY_BAR_NS

    def watch_bars(self, _old: list[tuple[int, float]], new: list[tuple[int, float]]) -> None:
        if new:
            self.replot()
    def replot(self) -> None:
        self.plt.clear_data()
        date_form, fmt = _axis_formats(self.bar_ns)
        self.plt.date_form(date_form)
        ds = [dt.datetime.fromtimestamp(t / 1e9).strftime(fmt) for t, _ in self.bars]
        self.plt.bar(ds, [v for _, v in self.bars])
        self.refresh()
