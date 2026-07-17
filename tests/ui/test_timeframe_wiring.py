import pytest

from entropy.app import AppConfig
from entropy.engine.timeframe import get_timeframe
from entropy.ui.app import EntropyApp


def test_appconfig_default_timeframe():
    assert AppConfig().timeframe == "15m"


@pytest.mark.asyncio
async def test_candle_interval_matches_timeframe():
    app = EntropyApp(AppConfig(enable_crypto=False, timeframe="15m"))
    async with app.run_test(size=(120, 60)):
        spec = get_timeframe("15m")
        assert app._price_candles.interval_ns == spec.bar_ns
        assert app._focus_candles.interval_ns == spec.bar_ns


@pytest.mark.asyncio
async def test_candle_interval_follows_nondefault_timeframe():
    app = EntropyApp(AppConfig(enable_crypto=False, timeframe="1h"))
    async with app.run_test(size=(120, 60)):
        spec = get_timeframe("1h")
        assert app._price_candles.interval_ns == spec.bar_ns
        assert app._focus_candles.interval_ns == spec.bar_ns
        assert app.engine.cfg.window_labels == ("1h", "4h", "1d")
