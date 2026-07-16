import pytest
from textual.widgets import Select

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets import modals
from entropy.ui.widgets.modals import SettingsScreen


def test_confirm_screen_removed():
    assert not hasattr(modals, "SettingsConfirmScreen")


@pytest.mark.asyncio
async def test_settings_has_timeframe_and_no_risk():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        tf = screen.query_one("#set-timeframe", Select)
        assert tf.value == "15m"
        from textual.css.query import NoMatches
        with pytest.raises(NoMatches):
            screen.query_one("#set-risk", Select)


@pytest.mark.asyncio
async def test_timeframe_change_hot_applies():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        screen = app.screen
        screen.query_one("#set-timeframe", Select).value = "1h"
        await pilot.click("#btn-save")
        await pilot.pause()
        assert app.cfg.timeframe == "1h"
        assert app._candle_interval_ns == 3_600_000_000_000
        assert app.cfg.engine.window_labels == ("1h", "4h", "1d")


@pytest.mark.asyncio
async def test_redraw_after_timeframe_switch_does_not_crash():
    # Switching timeframe rebuilds the engine + candle aggregators + gauge labels;
    # the periodic redraw path must keep working against the new state.
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        app.screen.query_one("#set-timeframe", Select).value = "1h"
        await pilot.click("#btn-save")
        await pilot.pause()
        # feed a couple of ticks through the (now 1h) engine and redraw
        app.engine.on_trade("SPY", 100.0, 1.0, "buy", 0)
        app.engine.on_trade("SPY", 101.0, 1.0, "buy", 1_000_000_000)
        app.sample_snapshot()  # exercises the full snapshot -> widget redraw path
        gauges = app.query_one("#hist")
        assert gauges.window_labels == ("1h", "4h", "1d")
