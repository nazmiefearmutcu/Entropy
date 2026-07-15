import pytest

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.highlow_gauges import HighLowGauges


@pytest.mark.asyncio
async def test_gauges_use_timeframe_labels():
    app = EntropyApp(AppConfig(enable_crypto=False, timeframe="15m"))
    async with app.run_test(size=(120, 60)):
        gauges = app.query_one("#hist", HighLowGauges)
        assert gauges.window_labels == ("15m", "1h", "4h")


@pytest.mark.asyncio
async def test_gauges_labels_follow_nondefault_timeframe():
    app = EntropyApp(AppConfig(enable_crypto=False, timeframe="1h"))
    async with app.run_test(size=(120, 60)):
        gauges = app.query_one("#hist", HighLowGauges)
        assert gauges.window_labels == ("1h", "4h", "1d")
