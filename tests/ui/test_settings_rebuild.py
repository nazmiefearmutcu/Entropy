import pytest
from textual.widgets import Input, Select

from entropy.app import AppConfig
from entropy.strategy.engine import Side
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


@pytest.mark.asyncio
async def test_settings_saveable_on_standard_terminal():
    # Regression: the sectioned settings form must stay saveable at a standard 80x24
    # terminal — the Save button must be on-screen (pilot.click raises OutOfBounds if not).
    app = EntropyApp(AppConfig(enable_crypto=False, enable_equities=False))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("s")
        app.screen.query_one("#set-timeframe", Select).value = "1h"
        await pilot.click("#btn-save")
        await pilot.pause()
        assert app.cfg.timeframe == "1h"


@pytest.mark.asyncio
async def test_timeframe_change_rebuilds_strategy_fresh():
    # Regression: a timeframe change must rebuild the strategies fresh (flat position),
    # not re-warm the live object in place (which stranded open positions / skewed the EMA).
    app = EntropyApp(AppConfig(enable_crypto=False, enable_equities=False))
    async with app.run_test(size=(80, 24)) as pilot:
        strat_before = app.strategy
        app.strategy.position.side = Side.LONG  # simulate a stranded open position
        await pilot.press("s")
        app.screen.query_one("#set-timeframe", Select).value = "5m"
        await pilot.click("#btn-save")
        await pilot.pause()
        assert app.strategy is not strat_before
        assert app.strategy.position.side is Side.FLAT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,bad", [("#set-tps", "-5"), ("#set-spike", "-0.5"), ("#set-tps", "0")]
)
async def test_out_of_range_inputs_rejected(field, bad):
    # Regression: negative/zero numeric inputs must be rejected (ErrorScreen), not applied.
    app = EntropyApp(AppConfig(enable_crypto=False, enable_equities=False))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("s")
        app.screen.query_one(field, Input).value = bad
        await pilot.click("#btn-save")
        await pilot.pause()
        assert app.screen.id == "errors"
        assert app.cfg.equity_tps == 4000
        assert app.cfg.engine.spike_pct == 0.40
