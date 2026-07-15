import pytest
from textual.widgets import Select, Input, Button

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.modals import SettingsScreen


@pytest.mark.asyncio
async def test_rapid_multiple_save_clicks_apply_once():
    """Posting several Save clicks back-to-back (before the event loop gets a
    chance to run) must not crash and must not double-apply — the `_saving`
    guard on SettingsScreen should make every click after the first a no-op."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"
        settings_screen = app.screen
        assert isinstance(settings_screen, SettingsScreen)

        settings_screen.query_one("#set-tps", Input).value = "1500"
        await pilot.pause()

        btn_save = settings_screen.query_one("#btn-save", Button)
        settings_screen.post_message(Button.Pressed(btn_save))
        settings_screen.post_message(Button.Pressed(btn_save))
        settings_screen.post_message(Button.Pressed(btn_save))
        await pilot.pause()

        # Screen closed cleanly; no leftover modal stacked from the extra clicks.
        assert app.screen.id != "settings"
        assert len(app.screen_stack) == 1
        assert app.cfg.equity_tps == 1500
        assert app._equity.tps == 1500

        await pilot.press("q")


@pytest.mark.asyncio
async def test_rapid_open_close_settings_screen_stack_stays_clean():
    """Rapidly opening and dismissing Settings must never leave more than one
    SettingsScreen (or any stray modal) on the stack."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        for _ in range(10):
            await pilot.press("s")
            await pilot.press("escape")
        await pilot.pause()

        assert app.screen.id != "settings"
        assert len(app.screen_stack) == 1

        settings_screens = [s for s in app.screen_stack if isinstance(s, SettingsScreen)]
        assert len(settings_screens) == 0

        await pilot.press("q")


@pytest.mark.asyncio
async def test_pressing_s_while_settings_open_does_not_stack_duplicates():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"

        # Pressing "s" again while Settings is already the active (modal) screen
        # is not bound on SettingsScreen, so it should be inert.
        await pilot.press("s")
        await pilot.pause()

        settings_screens = [s for s in app.screen_stack if isinstance(s, SettingsScreen)]
        assert len(settings_screens) == 1

        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")


@pytest.mark.asyncio
async def test_rapid_value_changes_after_save_posted_settle_without_crash():
    """Stress the async config-swap path: change several fields, post the save
    click, then keep mutating widget values before the event loop advances.
    The app must not crash and must land on one of the observed values."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"
        settings_screen = app.screen

        theme_select = settings_screen.query_one("#set-theme", Select)
        chart_select = settings_screen.query_one("#set-chart", Select)

        theme_select.value = "cyberpunk"
        chart_select.value = "line"

        btn_save = settings_screen.query_one("#btn-save", Button)
        settings_screen.post_message(Button.Pressed(btn_save))

        # Mutate again immediately, before the message loop has processed Save.
        theme_select.value = "forest"
        chart_select.value = "candlestick"

        await pilot.pause()

        assert app.screen.id != "settings"
        assert app.cfg.theme in ("cyberpunk", "forest")
        assert app.cfg.chart_type in ("line", "candlestick")
        assert app.theme == app.cfg.theme

        await pilot.press("q")


@pytest.mark.asyncio
async def test_repeated_full_save_cycles_stay_stable():
    """Open Settings, change the timeframe, save, and repeat several times —
    the hot-apply path (engine/candle rebuild) must stay stable under reuse."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    timeframes = ["1h", "5m", "1m", "4h", "15m"]
    async with app.run_test(size=(120, 60)) as pilot:
        for tf in timeframes:
            await pilot.press("s")
            await pilot.pause()
            assert app.screen.id == "settings"
            screen = app.screen
            screen.query_one("#set-timeframe", Select).value = tf
            await pilot.pause()
            await pilot.click("#btn-save")
            await pilot.pause()
            assert app.screen.id != "settings"
            assert app.cfg.timeframe == tf

        assert len(app.screen_stack) == 1
        await pilot.press("q")
