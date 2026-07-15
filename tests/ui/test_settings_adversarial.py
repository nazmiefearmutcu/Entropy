import pytest
import asyncio
from textual.widgets import Select, Switch, Input, Button
from textual.events import Key
from textual.css.query import NoMatches

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.modals import SettingsScreen, SettingsConfirmScreen, ErrorScreen
from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner
from entropy.bot.ui.app import BotDashboard
from entropy.bot.ui.confirm import BotSettingsScreen, ConfirmRiskScreen

@pytest.mark.asyncio
async def test_settings_screen_push_multiple_times_s():
    """Verify if pressing 's' multiple times opens multiple settings screens or errors out."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        # Press 's' to open Settings modal
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"
        
        # Press 's' again while open
        await pilot.press("s")
        await pilot.pause()
        
        # Count settings screens in the stack
        settings_screens = [s for s in app.screen_stack if isinstance(s, SettingsScreen)]
        print(f"EntropyApp SettingsScreen instances on stack: {len(settings_screens)}")
        
        # Assert only one exists or it handles it gracefully without nesting settings screen
        assert len(settings_screens) == 1, "Multiple SettingsScreens pushed to stack!"
        
        # Exit cleanly
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")


@pytest.mark.asyncio
async def test_bot_dashboard_settings_screen_push_multiple_times_s(tmp_path):
    """Verify if pressing 's' multiple times in BotDashboard opens multiple settings screens."""
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        # Press 's' to open Settings modal
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)
        
        # Press 's' again
        await pilot.press("s")
        await pilot.pause()
        
        bot_settings_screens = [s for s in app.screen_stack if isinstance(s, BotSettingsScreen)]
        print(f"BotDashboard BotSettingsScreen instances on stack: {len(bot_settings_screens)}")
        
        assert len(bot_settings_screens) == 1, "Multiple BotSettingsScreens pushed to stack in BotDashboard!"
        
        # Exit cleanly
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_bot_settings_confirm_screen_multiple_clicks(tmp_path):
    """Stress test the save flow in BotSettingsScreen by rapidly posting save clicks."""
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        # Open Settings
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)
        
        # Select 'extreme' to trigger confirmation screen on save
        select = app.screen.query_one(Select)
        select.value = "extreme"
        await pilot.pause()
        
        # Post multiple Button.Pressed events before letting event loop run
        btn_save = app.screen.query_one("#btn-save", Button)
        app.screen.post_message(Button.Pressed(btn_save))
        app.screen.post_message(Button.Pressed(btn_save))
        app.screen.post_message(Button.Pressed(btn_save))
        
        # Let the event loop process these messages
        await pilot.pause()
        
        # Count confirmation screens
        confirm_screens = [s for s in app.screen_stack if isinstance(s, ConfirmRiskScreen)]
        print(f"BotDashboard ConfirmRiskScreen instances on stack: {len(confirm_screens)}")
        
        # Verify that only one confirmation screen gets pushed
        assert len(confirm_screens) == 1, f"Found {len(confirm_screens)} ConfirmRiskScreen instances on stack!"
        
        # Dismiss and exit
        await pilot.click("#cancel")
        await pilot.pause()
        await pilot.click("#btn-cancel")
        await pilot.pause()


@pytest.mark.asyncio
async def test_keybindings_1_2_3_custom_events(tmp_path):
    """Verify that keypresses/events for '1', '2', '3' are fully ignored."""
    # Test EntropyApp
    app1 = EntropyApp(AppConfig(enable_crypto=False))
    async with app1.run_test() as pilot:
        # Dispatch key events directly to the app
        app1.post_message(Key(key="1", character="1"))
        app1.post_message(Key(key="2", character="2"))
        app1.post_message(Key(key="3", character="3"))
        await pilot.pause()
        
        # Ensure no screens were opened
        assert app1.screen.id != "settings"
        assert not isinstance(app1.screen, SettingsConfirmScreen)
        await pilot.press("q")
        
    # Test BotDashboard
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app2 = BotDashboard(cfg, runner=bot)
    async with app2.run_test() as pilot:
        app2.post_message(Key(key="1", character="1"))
        app2.post_message(Key(key="2", character="2"))
        app2.post_message(Key(key="3", character="3"))
        await pilot.pause()
        
        # Ensure no risk changes occurred and no confirmation modal opened
        assert bot.risk.profile.name == "Frosty"
        assert not isinstance(app2.screen, ConfirmRiskScreen)


@pytest.mark.asyncio
async def test_rapid_state_transitions_invalid_inputs():
    """Verify rapid and invalid state updates in SettingsScreen."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        # 1. Open and immediately try to save invalid inputs
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        tps_input = settings_screen.query_one("#set-tps", Input)
        spike_input = settings_screen.query_one("#set-spike", Input)
        
        # Set invalid values
        tps_input.value = "not-an-integer"
        spike_input.value = "not-a-float"
        await pilot.pause()
        
        # Save changes
        await pilot.click("#btn-save")
        await pilot.pause()
        
        # Check that ErrorScreen is displayed
        assert isinstance(app.screen, ErrorScreen)
        assert app.screen.id == "errors"
        
        # Dismiss error screen
        await pilot.press("escape")
        await pilot.pause()
        
        # We should be back on settings screen
        assert app.screen == settings_screen
        
        # Correct the inputs and check rapid saves
        tps_input.value = "3000"
        spike_input.value = "0.2"
        await pilot.pause()
        
        # Click save rapidly
        btn_save = settings_screen.query_one("#btn-save", Button)
        settings_screen.post_message(Button.Pressed(btn_save))
        settings_screen.post_message(Button.Pressed(btn_save))
        await pilot.pause()
        
        # Verify SettingsScreen has closed and settings were updated
        assert app.screen.id != "settings"
        assert app.cfg.equity_tps == 3000
        assert app.cfg.engine.spike_pct == 0.2
        
        await pilot.press("q")


@pytest.mark.asyncio
async def test_bot_settings_confirm_screen_cancel_and_retry(tmp_path):
    """Verify that canceling the ConfirmRiskScreen resets the saving state, allowing retry."""
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        # Open Settings
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)
        settings_screen = app.screen

        # Select 'extreme' to trigger confirmation screen
        select = settings_screen.query_one(Select)
        select.value = "extreme"
        await pilot.pause()

        # Click save
        await pilot.click("#btn-save")
        await pilot.pause()

        # Verify confirmation screen is active
        confirm_screen = app.screen
        assert isinstance(confirm_screen, ConfirmRiskScreen)

        # Click cancel on confirm screen
        await pilot.click("#cancel")
        await pilot.pause()

        # We should be back on BotSettingsScreen
        assert app.screen == settings_screen
        # Config should still be frosty
        assert bot.risk.profile.name == "Frosty"

        # Click save again
        await pilot.click("#btn-save")
        await pilot.pause()

        # Verify confirmation screen is active again
        confirm_screen = app.screen
        assert isinstance(confirm_screen, ConfirmRiskScreen)

        # Click confirm on confirm screen
        await pilot.click("#confirm")
        await pilot.pause()

        # Both screens should be dismissed
        assert not isinstance(app.screen, BotSettingsScreen)
        assert not isinstance(app.screen, ConfirmRiskScreen)
        # Risk profile should now be extreme
        assert bot.risk.profile.name == "Extreme"


@pytest.mark.asyncio
async def test_bot_settings_escape_dismiss(tmp_path):
    """Verify that pressing 'escape' on BotSettingsScreen dismisses the settings screen."""
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)
        
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, BotSettingsScreen)


@pytest.mark.asyncio
async def test_confirm_risk_escape_cancel(tmp_path):
    """Verify that pressing 'escape' on ConfirmRiskScreen cancels and triggers the callback, resetting saving state."""
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)
        
        # Change risk to extreme
        select = app.screen.query_one(Select)
        select.value = "extreme"
        await pilot.pause()
        
        # Save to trigger confirmation screen
        await pilot.click("#btn-save")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRiskScreen)
        
        # Press escape to cancel the confirmation screen
        await pilot.press("escape")
        await pilot.pause()
        
        # Should be back on BotSettingsScreen and saving flag is reset
        assert isinstance(app.screen, BotSettingsScreen)
        
        # Click save again to verify we can re-open confirmation screen
        await pilot.click("#btn-save")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRiskScreen)

