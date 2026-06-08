from __future__ import annotations

from dataclasses import dataclass

from textual.reactive import reactive
from textual_plotext import PlotextPlot


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
    def watch_candles(self, _old: list[Candle], new: list[Candle]) -> None:
        if new:
            self.replot()
    def replot(self) -> None:
        import datetime as dt
        self.plt.clear_data()
        # 1-second candles — use HH:MM:SS so every bar gets a unique x-label.
        self.plt.date_form("H:M:S")
        ds = [dt.datetime.fromtimestamp(c.t / 1e9).strftime("%H:%M:%S") for c in self.candles]
        data = {"Open": [c.o for c in self.candles], "Close": [c.c for c in self.candles],
                "High": [c.h for c in self.candles], "Low": [c.l for c in self.candles]}
        self.plt.candlestick(ds, data)
        self.refresh()

class VolumeChart(PlotextPlot):
    bars: reactive[list[tuple[int, float]]] = reactive(list, always_update=True)
    def watch_bars(self, _old: list[tuple[int, float]], new: list[tuple[int, float]]) -> None:
        if new:
            self.replot()
    def replot(self) -> None:
        import datetime as dt
        self.plt.clear_data()
        self.plt.date_form("H:M:S")
        ds = [dt.datetime.fromtimestamp(t / 1e9).strftime("%H:%M:%S") for t, _ in self.bars]
        self.plt.bar(ds, [v for _, v in self.bars])
        self.refresh()
