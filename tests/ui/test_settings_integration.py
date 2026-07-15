import pytest
from textual.widgets import Select, Switch, Input

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.modals import SettingsScreen
from entropy.ui.widgets.charts import PriceChart, VolumeChart


@pytest.mark.asyncio
async def test_settings_save_flow_hot_applies_appearance_and_feeds():
    """Open Settings, change theme/chart/volume/TPS, save, and verify both
    AppConfig and the live widgets picked up the change without a restart."""
    app = EntropyApp(AppConfig(enable_crypto=False))

    async with app.run_test(size=(120, 60)) as pilot:
        assert app.cfg.theme == "entropy"
        assert app.theme == "entropy"
        assert app.cfg.chart_type == "candlestick"
        assert app.cfg.show_volume is True
        assert app.cfg.equity_tps == 4000

        price_chart = app.query_one("#price", PriceChart)
        price_chart2 = app.query_one("#price2", PriceChart)
        volume_chart = app.query_one("#volume", VolumeChart)
        volume_chart2 = app.query_one("#volume2", VolumeChart)

        await pilot.press("s")
        assert app.screen.id == "settings"
        settings_screen = app.screen
        assert isinstance(settings_screen, SettingsScreen)

        settings_screen.query_one("#set-theme", Select).value = "dracula"
        settings_screen.query_one("#set-chart", Select).value = "line"
        settings_screen.query_one("#set-volume", Switch).value = False
        settings_screen.query_one("#set-tps", Input).value = "2500"
        settings_screen.query_one("#set-spike", Input).value = "0.12"
        await pilot.pause()

        await pilot.click("#btn-save")
        await pilot.pause()

        # Modal closed with a single save click (no confirmation step anymore).
        assert app.screen.id != "settings"

        assert app.cfg.theme == "dracula"
        assert app.cfg.chart_type == "line"
        assert app.cfg.show_volume is False
        assert app.cfg.equity_tps == 2500
        assert app.cfg.engine.spike_pct == 0.12

        assert app.theme == "dracula"
        assert price_chart.chart_type == "line"
        assert price_chart2.chart_type == "line"
        assert volume_chart.display is False
        assert volume_chart2.display is False
        assert app._equity.tps == 2500
        assert app.engine.cfg.spike_pct == 0.12

        await pilot.press("q")


@pytest.mark.asyncio
async def test_settings_no_risk_field_and_config_stays_medium():
    """Risk Management Mode was removed from Settings entirely; risk_profile
    stays at its default and is never touched by a settings save."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.cfg.risk_profile == "medium"

        await pilot.press("s")
        settings_screen = app.screen
        from textual.css.query import NoMatches
        with pytest.raises(NoMatches):
            settings_screen.query_one("#set-risk")

        # A normal save shouldn't disturb risk_profile.
        settings_screen.query_one("#set-tps", Input).value = "1234"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.cfg.risk_profile == "medium"
        assert app.cfg.equity_tps == 1234
        await pilot.press("q")


@pytest.mark.asyncio
async def test_timeframe_change_reconfigures_candles_and_engine():
    """Changing the Timeframe selector and saving hot-reconfigures the candle
    aggregators + engine window labels — the new hot-apply surface added by
    this migration."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.cfg.timeframe == "15m"
        old_price_agg = app._price_candles
        old_crypto_agg = app._crypto_candles

        await pilot.press("s")
        settings_screen = app.screen
        settings_screen.query_one("#set-timeframe", Select).value = "5m"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.cfg.timeframe == "5m"
        assert app.cfg.engine.window_labels == ("5m", "15m", "1h")
        assert app.engine.cfg.window_labels == ("5m", "15m", "1h")
        assert app._candle_interval_ns == 5 * 60 * 1_000_000_000
        assert app._price_candles.interval_ns == 5 * 60 * 1_000_000_000
        assert app._crypto_candles.interval_ns == 5 * 60 * 1_000_000_000
        # New aggregators were built for the new timeframe, not mutated in place.
        assert app._price_candles is not old_price_agg
        assert app._crypto_candles is not old_crypto_agg

        await pilot.press("q")


@pytest.mark.asyncio
async def test_unchanged_timeframe_keeps_same_engine_object():
    """Saving without touching the Timeframe selector must not tear down and
    rebuild the engine/candle aggregators — only the config on it updates."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        engine_before = app.engine
        price_agg_before = app._price_candles

        await pilot.press("s")
        settings_screen = app.screen
        settings_screen.query_one("#set-snapdrop", Input).value = "0.25"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.cfg.timeframe == "15m"
        assert app.engine is engine_before
        assert app._price_candles is price_agg_before
        assert app.engine.cfg.snapdrop_pct == 0.25

        await pilot.press("q")
