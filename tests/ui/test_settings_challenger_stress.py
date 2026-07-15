import pytest
import asyncio
from textual.widgets import Select, Switch, Input, Button
from textual import events

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.modals import SettingsScreen, SettingsConfirmScreen, ErrorScreen
from entropy.ui.widgets.charts import PriceChart, VolumeChart

from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner
from entropy.bot.ui.app import BotDashboard
from entropy.bot.ui.confirm import BotSettingsScreen, ConfirmRiskScreen

@pytest.mark.asyncio
async def test_rapid_settings_toggle_and_stack_transitions():
    """Stress test rapid toggling of the settings screen and stack transitions."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        # Rapidly open and close Settings screen to see if stack gets corrupted
        for _ in range(5):
            await pilot.press("s")
            # Immediately press escape to dismiss
            await pilot.press("escape")
        
        await pilot.pause()
        # Ensure we are back to the default screen
        assert app.screen.id != "settings"
        assert len(app.screen_stack) == 1

        # Now test invalid input error screen transition
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        assert isinstance(settings_screen, SettingsScreen)
        
        # Insert invalid input in TPS input to trigger ValueError
        tps_input = settings_screen.query_one("#set-tps", Input)
        tps_input.value = "invalid_number"
        await pilot.pause()
        
        # Click save to trigger ErrorScreen
        await pilot.click("#btn-save")
        await pilot.pause()
        
        # Verify ErrorScreen is open
        assert isinstance(app.screen, ErrorScreen)
        assert app.screen.id == "errors"
        
        # Rapidly dismiss the ErrorScreen and open settings again
        await pilot.press("escape")
        await pilot.pause()
        
        # We should be back on the SettingsScreen
        assert app.screen == settings_screen
        
        # Dismiss SettingsScreen
        await pilot.click("#btn-cancel")
        await pilot.pause()
        assert app.screen.id != "settings"

@pytest.mark.asyncio
async def test_keybindings_1_2_3_disabled_and_untriggerable():
    """Verify that keybindings 1, 2, 3 do not trigger actions or changes on both EntropyApp and BotDashboard."""
    
    # 1. Test on EntropyApp
    app = EntropyApp(AppConfig(enable_crypto=False, risk_profile="medium"))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        
        # Try sending direct key presses
        for key in ["1", "2", "3"]:
            await pilot.press(key)
            await pilot.pause()
            # Verify no change in risk profile config
            assert app.cfg.risk_profile == "medium"
            # Verify no settings/confirm screen opened
            assert not isinstance(app.screen, SettingsConfirmScreen)
            assert not isinstance(app.screen, SettingsScreen)
        
        # Try custom event dispatches (events.Key) directly posted to the App
        for key in ["1", "2", "3"]:
            app.post_message(events.Key(key=key, character=key))
            await pilot.pause()
            assert app.cfg.risk_profile == "medium"
            assert not isinstance(app.screen, SettingsConfirmScreen)
        
        # Try custom event dispatches when SettingsScreen is active
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"
        settings_screen = app.screen
        
        for key in ["1", "2", "3"]:
            app.post_message(events.Key(key=key, character=key))
            settings_screen.post_message(events.Key(key=key, character=key))
            await pilot.pause()
            assert app.cfg.risk_profile == "medium"
            assert not isinstance(app.screen, SettingsConfirmScreen)
            
        await pilot.click("#btn-cancel")
        await pilot.pause()
        await pilot.press("q")

    # 2. Test on BotDashboard
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="medium")
    bot = BotRunner(cfg)
    bot_app = BotDashboard(cfg, runner=bot)
    async with bot_app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        
        # Try sending direct key presses
        for key in ["1", "2", "3"]:
            await pilot.press(key)
            await pilot.pause()
            # Verify risk profile name remains "Medium"
            assert bot.risk.profile.name == "Medium"
            assert not isinstance(bot_app.screen, ConfirmRiskScreen)
            assert not isinstance(bot_app.screen, BotSettingsScreen)
            
        # Try custom event dispatches directly posted to the App
        for key in ["1", "2", "3"]:
            bot_app.post_message(events.Key(key=key, character=key))
            await pilot.pause()
            assert bot.risk.profile.name == "Medium"
            assert not isinstance(bot_app.screen, ConfirmRiskScreen)
            
        # Try custom event dispatches when BotSettingsScreen is active
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(bot_app.screen, BotSettingsScreen)
        bot_settings = bot_app.screen
        
        for key in ["1", "2", "3"]:
            bot_app.post_message(events.Key(key=key, character=key))
            bot_settings.post_message(events.Key(key=key, character=key))
            await pilot.pause()
            assert bot.risk.profile.name == "Medium"
            assert not isinstance(bot_app.screen, ConfirmRiskScreen)
            
        await pilot.click("#btn-cancel")
        await pilot.pause()
        await pilot.press("q")

@pytest.mark.asyncio
async def test_rapid_state_transitions_and_config_updates():
    """Verify the UI handles rapid state transitions and config updates gracefully."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        # We will change a setting, save, open setting immediately, change, save, without delay.
        # This stresses the async task spawning/cancellation and dynamic updates.
        
        # Open Settings modal
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        
        # Switch values rapidly
        theme_select = settings_screen.query_one("#set-theme", Select)
        chart_select = settings_screen.query_one("#set-chart", Select)
        volume_switch = settings_screen.query_one("#set-volume", Switch)
        tps_input = settings_screen.query_one("#set-tps", Input)
        
        # Rapid changes
        theme_select.value = "cyberpunk"
        chart_select.value = "line"
        volume_switch.value = False
        tps_input.value = "1000"
        
        # Post button save pressed
        btn_save = settings_screen.query_one("#btn-save", Button)
        settings_screen.post_message(Button.Pressed(btn_save))
        
        # Immediately change value again if possible (simulating rapid user interaction or state changes before tick)
        theme_select.value = "forest"
        chart_select.value = "candlestick"
        
        await pilot.pause()
        
        # The modal should have saved and closed
        assert app.screen.id != "settings"
        
        # Verify that the final applied values are from the last state of the Select fields before the save processed
        assert app.cfg.theme in ("cyberpunk", "forest")
        assert app.cfg.chart_type in ("line", "candlestick")
        
        # Open Settings again and perform quick cancel
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.id == "settings"
        
        await pilot.click("#btn-cancel")
        await pilot.pause()
        assert app.screen.id != "settings"
        
        await pilot.press("q")
