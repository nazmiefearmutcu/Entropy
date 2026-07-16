import pytest
from textual.widgets import Button, Input

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.modals import ErrorScreen, SettingsScreen


async def _open_settings(pilot, app):
    await pilot.press("s")
    await pilot.pause()
    assert app.screen.id == "settings"
    screen = app.screen
    assert isinstance(screen, SettingsScreen)
    return screen


@pytest.mark.asyncio
async def test_invalid_tps_shows_error_and_does_not_crash():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open_settings(pilot, app)

        screen.query_one("#set-tps", Input).value = "not-an-integer"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert isinstance(app.screen, ErrorScreen)
        assert app.screen.id == "errors"
        # Nothing was applied.
        assert app.cfg.equity_tps == 4000

        await pilot.press("q")


@pytest.mark.asyncio
async def test_invalid_spike_pct_shows_error_and_does_not_crash():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open_settings(pilot, app)

        screen.query_one("#set-spike", Input).value = "not-a-float"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert isinstance(app.screen, ErrorScreen)
        assert app.screen.id == "errors"
        assert app.cfg.engine.spike_pct == 0.40

        await pilot.press("q")


@pytest.mark.asyncio
async def test_invalid_snapdrop_pct_shows_error_and_does_not_crash():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open_settings(pilot, app)

        screen.query_one("#set-snapdrop", Input).value = "also-not-a-float"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert isinstance(app.screen, ErrorScreen)
        assert app.screen.id == "errors"
        assert app.cfg.engine.snapdrop_pct == 0.40

        await pilot.press("q")


@pytest.mark.asyncio
async def test_error_screen_dismiss_returns_to_settings_and_saving_flag_resets():
    """After an invalid save the `_saving` guard must reset so a subsequent
    (valid) save from the same SettingsScreen instance still works."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open_settings(pilot, app)

        tps_input = screen.query_one("#set-tps", Input)
        spike_input = screen.query_one("#set-spike", Input)

        tps_input.value = "garbage"
        spike_input.value = "garbage"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert isinstance(app.screen, ErrorScreen)
        assert screen._saving is False

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen == screen

        # Fix the inputs and save again — should succeed now.
        tps_input.value = "3000"
        spike_input.value = "0.2"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.screen.id != "settings"
        assert app.cfg.equity_tps == 3000
        assert app.cfg.engine.spike_pct == 0.2

        await pilot.press("q")


@pytest.mark.asyncio
async def test_multiple_invalid_fields_still_single_error_screen():
    """Multiple simultaneously-invalid fields should still fail cleanly with
    exactly one ErrorScreen — not a crash, not multiple stacked screens."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open_settings(pilot, app)

        screen.query_one("#set-tps", Input).value = "xx"
        screen.query_one("#set-spike", Input).value = "yy"
        screen.query_one("#set-snapdrop", Input).value = "zz"
        await pilot.pause()

        btn_save = screen.query_one("#btn-save", Button)
        screen.post_message(Button.Pressed(btn_save))
        await pilot.pause()

        error_screens = [s for s in app.screen_stack if isinstance(s, ErrorScreen)]
        assert len(error_screens) == 1
        assert app.cfg.equity_tps == 4000

        await pilot.press("escape")
        await pilot.pause()
        await pilot.click("#btn-cancel")
        await pilot.pause()
        await pilot.press("q")
