import datetime as dt

import pytest

from entropy.ui.widgets.charts import Candle, PriceChart, VolumeChart, _axis_formats

_MIN_NS = 60 * 1_000_000_000
_HOUR_NS = 60 * _MIN_NS
_DAY_NS = 24 * _HOUR_NS


def test_axis_formats_tiers():
    # sub-hour bars (legacy 1s, 1m/5m/15m timeframes): intraday HH:MM
    assert _axis_formats(1_000_000_000) == ("H:M", "%H:%M")
    assert _axis_formats(15 * _MIN_NS) == ("H:M", "%H:%M")
    assert _axis_formats(_HOUR_NS - 1) == ("H:M", "%H:%M")
    # hour-scale bars (1h/4h): span midnights, so prefix the day
    assert _axis_formats(_HOUR_NS) == ("d/m H:M", "%d/%m %H:%M")
    assert _axis_formats(4 * _HOUR_NS) == ("d/m H:M", "%d/%m %H:%M")
    assert _axis_formats(_DAY_NS - 1) == ("d/m H:M", "%d/%m %H:%M")
    # day-scale and coarser: date only
    assert _axis_formats(_DAY_NS) == ("d/m/Y", "%d/%m/%Y")
    assert _axis_formats(7 * _DAY_NS) == ("d/m/Y", "%d/%m/%Y")


def test_axis_formats_pairs_stay_parseable_by_plotext():
    """plotext's date_form() inserts a '%' before every letter; each returned pair
    must stay letter-for-letter in sync so the strings replot() renders are
    parseable under the date_form it sets."""
    from plotext._date import date_class

    d = date_class()
    sample = dt.datetime(2026, 7, 16, 13, 45)
    for bar_ns in (1_000_000_000, 15 * _MIN_NS, _HOUR_NS, 4 * _HOUR_NS, _DAY_NS):
        form, fmt = _axis_formats(bar_ns)
        assert d.correct_form(form) == fmt
        d.string_to_time(sample.strftime(fmt), form)  # raises ValueError on mismatch


@pytest.mark.asyncio
async def test_price_chart_accepts_candles():
    from textual.app import App, ComposeResult

    class _A(App):
        def compose(self) -> ComposeResult:
            yield PriceChart(id="price")

    app = _A()
    # Use realistic intraday ns timestamps so date formatting exercises real values
    _BASE = 1_700_000_000_000_000_000
    _STEP = 60_000_000_000  # 1 minute in ns
    async with app.run_test():
        chart = app.query_one("#price", PriceChart)
        candles = [Candle(t=_BASE + i * _STEP, o=10, h=11, l=9, c=10.5) for i in range(20)]
        # Assignment triggers watch_candles -> replot(); if replot() raises,
        # the reactive watcher propagates the exception and this line fails.
        chart.candles = candles
        assert len(chart.candles) == 20
        # Confirm the first candle's timestamp is preserved (replot doesn't mutate data)
        assert chart.candles[0].t == _BASE


@pytest.mark.asyncio
async def test_volume_chart_accepts_bars():
    from textual.app import App, ComposeResult

    class _A(App):
        def compose(self) -> ComposeResult:
            yield VolumeChart(id="volume")

    app = _A()
    async with app.run_test():
        chart = app.query_one("#volume", VolumeChart)
        chart.bars = [(i * 1_000_000_000, float(i)) for i in range(10)]
        assert len(chart.bars) == 10


@pytest.mark.asyncio
async def test_charts_replot_with_hourly_and_daily_bar_ns():
    """Setting bar_ns to hour/day-scale bars must replot cleanly with the
    coarser axis formats (regression: axis was hardcoded to H:M:S seconds)."""
    from textual.app import App, ComposeResult

    class _A(App):
        def compose(self) -> ComposeResult:
            yield PriceChart(id="price")
            yield VolumeChart(id="volume")

    app = _A()
    _BASE = 1_700_000_000_000_000_000
    async with app.run_test():
        price = app.query_one("#price", PriceChart)
        volume = app.query_one("#volume", VolumeChart)
        assert price.bar_ns == 1_000_000_000  # legacy second-scale default
        for bar_ns in (_HOUR_NS, 4 * _HOUR_NS, _DAY_NS):
            price.bar_ns = bar_ns
            volume.bar_ns = bar_ns
            # Assignment triggers watch_* -> replot(); a format/parse mismatch
            # inside plotext would raise out of the reactive watcher here.
            price.candles = [
                Candle(t=_BASE + i * bar_ns, o=10, h=11, l=9, c=10.5) for i in range(30)
            ]
            volume.bars = [(_BASE + i * bar_ns, float(i)) for i in range(30)]
