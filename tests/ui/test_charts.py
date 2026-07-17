import datetime as dt

import pytest

from entropy.ui.widgets.charts import (
    Candle,
    PriceChart,
    VolumeChart,
    _axis_formats,
    align_overlay,
    split_volume,
)

_MIN_NS = 60 * 1_000_000_000
_HOUR_NS = 60 * _MIN_NS
_DAY_NS = 24 * _HOUR_NS


def test_axis_formats_tiers():
    # The tier keys on the FULL CHART SPAN (bar_ns * 120 deque slots), not the
    # bar alone: 120 x 15m = 30h crosses a midnight, so "H:M" would wrap.
    # span within one day (legacy 1s, 1m/5m): intraday HH:MM stays unique
    assert _axis_formats(1_000_000_000) == ("H:M", "%H:%M")
    assert _axis_formats(_MIN_NS) == ("H:M", "%H:%M")
    assert _axis_formats(5 * _MIN_NS) == ("H:M", "%H:%M")
    assert _axis_formats(12 * _MIN_NS) == ("H:M", "%H:%M")  # 120 bars = exactly 24h
    # span crosses a midnight (15m/1h/4h): prefix the day to stay unique
    assert _axis_formats(15 * _MIN_NS) == ("d/m H:M", "%d/%m %H:%M")
    assert _axis_formats(_HOUR_NS) == ("d/m H:M", "%d/%m %H:%M")
    assert _axis_formats(4 * _HOUR_NS) == ("d/m H:M", "%d/%m %H:%M")
    assert _axis_formats(_DAY_NS - 1) == ("d/m H:M", "%d/%m %H:%M")
    # day-scale and coarser bars: date only
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


def test_align_overlay_shorter_series_right_aligned():
    # EMA warmup makes overlays shorter than the candle list: the front is
    # padded so the LAST value sits on the LAST candle.
    assert align_overlay(5, [1.0, 2.0]) == [None, None, None, 1.0, 2.0]


def test_align_overlay_equal_length_passthrough():
    assert align_overlay(3, [1.0, 2.0, 3.0]) == [1.0, 2.0, 3.0]


def test_align_overlay_empty_values_all_none():
    assert align_overlay(4, []) == [None, None, None, None]


def test_align_overlay_longer_series_keeps_tail():
    assert align_overlay(2, [1.0, 2.0, 3.0]) == [2.0, 3.0]


def test_align_overlay_no_candles():
    assert align_overlay(0, [1.0, 2.0]) == []


def test_split_volume_zero_fills_opposite_series():
    up, down = split_volume([5.0, 2.0, 3.0], [True, False, True])
    assert up == [5.0, 0.0, 3.0]
    assert down == [0.0, 2.0, 0.0]


def test_split_volume_empty():
    assert split_volume([], []) == ([], [])


def test_split_volume_length_mismatch_raises():
    # strict zip: callers (VolumeChart.set_series) must pre-validate lengths.
    with pytest.raises(ValueError):
        split_volume([1.0, 2.0], [True])


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
async def test_price_chart_set_series_draws_overlays():
    from textual.app import App, ComposeResult

    class _A(App):
        def compose(self) -> ComposeResult:
            yield PriceChart(id="price")

    app = _A()
    _BASE = 1_700_000_000_000_000_000
    _STEP = 60_000_000_000
    async with app.run_test():
        chart = app.query_one("#price", PriceChart)
        candles = [Candle(t=_BASE + i * _STEP, o=10, h=11, l=9, c=10.5) for i in range(20)]
        overlays = {
            "EMA9": [10.0 + i * 0.01 for i in range(20)],   # equal length
            "EMA21": [10.2, 10.3, 10.4],                    # shorter: right-aligned
            "EMA50": [10.1],                                # single point
        }
        for chart_type in ("candlestick", "line"):
            chart.chart_type = chart_type
            # set_series triggers watch_candles -> replot(); a plotext failure
            # (None values, x/y length mismatch) would raise out of the watcher.
            chart.set_series(candles, overlays)
        assert chart.overlays == overlays
        assert len(chart.candles) == 20
        # overlays cleared again by a plain draw
        chart.set_series(candles, None)
        assert chart.overlays is None


@pytest.mark.asyncio
async def test_volume_chart_set_series_up_down_split():
    from textual.app import App, ComposeResult

    class _A(App):
        def compose(self) -> ComposeResult:
            yield VolumeChart(id="volume")

    app = _A()
    _BASE = 1_700_000_000_000_000_000
    async with app.run_test():
        chart = app.query_one("#volume", VolumeChart)
        bars = [(_BASE + i * 1_000_000_000, float(i + 1)) for i in range(10)]
        ups = [i % 2 == 0 for i in range(10)]
        chart.set_series(bars, ups)   # two-series replot must not raise
        assert chart.ups == ups
        # Length-mismatched ups are dropped (single-series fallback), not trusted.
        chart.set_series(bars, [True])
        assert chart.ups is None
        # Legacy bare assignment (candle data without opens) keeps working.
        chart.bars = bars
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
