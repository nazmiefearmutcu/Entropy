import pytest
from textual.widgets import Select, Switch, Input

from entropy.app import AppConfig
from entropy.config import EngineConfig
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


@pytest.mark.asyncio
async def test_combined_timeframe_and_symbol_change_warms_once(monkeypatch):
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)):
        calls = {"n": 0}
        orig = app._warmup_strategies
        def counting() -> None:
            calls["n"] += 1
            orig()
        monkeypatch.setattr(app, "_warmup_strategies", counting)
        app._apply_settings(
            theme="entropy", chart_type="candlestick", show_volume=True,
            timeframe="1h", enable_equities=True, enable_crypto=False, equity_tps=4000,
            strategy_symbol="QQQ", crypto_strategy_symbol=app.cfg.crypto_strategy_symbol,
            spike_pct=0.40, snapdrop_pct=0.40,
        )
        assert app.cfg.timeframe == "1h"
        assert app.strategy.cfg.symbol == "QQQ"
        assert calls["n"] == 1  # single warmup despite timeframe AND symbol both changing


@pytest.mark.asyncio
async def test_apply_settings_preserves_non_form_engine_fields():
    """A timeframe change overlays the tf-derived windows/scalars + form
    spike/snapdrop onto the EXISTING engine config; non-form fields that the
    Settings form never surfaces (upmove/downmove/leaderboard_k/accel_eps)
    must survive untouched rather than reset to their bare defaults."""
    custom_engine = EngineConfig(
        upmove_pct=0.99, downmove_pct=0.88, leaderboard_k=7, accel_eps=0.42,
    )
    app = EntropyApp(AppConfig(enable_crypto=False, engine=custom_engine))
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.cfg.engine.leaderboard_k == 7

        await pilot.press("s")
        settings_screen = app.screen
        settings_screen.query_one("#set-timeframe", Select).value = "1h"
        settings_screen.query_one("#set-spike", Input).value = "0.5"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        eng = app.cfg.engine
        # Form + timeframe-derived fields updated...
        assert eng.spike_pct == 0.5
        assert eng.window_labels == ("1h", "4h", "1d")
        # ...but the non-form fields were preserved, not reset to defaults.
        assert eng.upmove_pct == 0.99
        assert eng.downmove_pct == 0.88
        assert eng.leaderboard_k == 7
        assert eng.accel_eps == 0.42
        # The live engine object carries the same preserved config.
        assert app.engine.cfg.leaderboard_k == 7

        await pilot.press("q")
