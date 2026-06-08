import pytest
from entropy.ui.widgets.charts import Candle, PriceChart

@pytest.mark.asyncio
async def test_price_chart_accepts_candles():
    from textual.app import App, ComposeResult
    class _A(App):
        def compose(self) -> ComposeResult:
            yield PriceChart(id="price")
    app = _A()
    async with app.run_test():
        chart = app.query_one("#price", PriceChart)
        chart.candles = [Candle(t=i, o=10, h=11, l=9, c=10.5) for i in range(20)]
        await app.workers.wait_for_complete() if False else None
        assert len(chart.candles) == 20
