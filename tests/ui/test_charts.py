import pytest

from entropy.ui.widgets.charts import Candle, PriceChart, VolumeChart


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
