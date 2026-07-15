import pytest
from textual.widgets import Select, Switch, Input, Button

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.modals import SettingsScreen, SettingsConfirmScreen
from entropy.ui.widgets.charts import PriceChart, VolumeChart


@pytest.mark.asyncio
async def test_settings_save_flow_and_hot_reload():
    # Initialize app with crypto disabled to prevent networking/websocket calls
    app = EntropyApp(AppConfig(enable_crypto=False))
    
    async with app.run_test(size=(120, 60)) as pilot:
        # Check default config values at start
        assert app.cfg.theme == "entropy"
        assert app.theme == "entropy"
        assert app.cfg.chart_type == "candlestick"
        assert app.cfg.show_volume is True
        assert app.cfg.equity_tps == 4000
        assert app.cfg.engine.spike_pct == 0.40
        
        # Verify initial reactive values of price and volume charts
        price_chart = app.query_one("#price", PriceChart)
        price_chart2 = app.query_one("#price2", PriceChart)
        volume_chart = app.query_one("#volume", VolumeChart)
        volume_chart2 = app.query_one("#volume2", VolumeChart)
        
        assert price_chart.chart_type == "candlestick"
        assert price_chart2.chart_type == "candlestick"
        assert volume_chart.display is True
        assert volume_chart2.display is True
        
        # Open Settings modal
        await pilot.press("s")
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        assert isinstance(settings_screen, SettingsScreen)
        
        # Find UI elements inside SettingsScreen modal
        theme_select = settings_screen.query_one("#set-theme", Select)
        chart_select = settings_screen.query_one("#set-chart", Select)
        volume_switch = settings_screen.query_one("#set-volume", Switch)
        tps_input = settings_screen.query_one("#set-tps", Input)
        spike_input = settings_screen.query_one("#set-spike", Input)
        
        # Set theme to 'dracula', chart style to 'line', show volume to False
        theme_select.value = "dracula"
        chart_select.value = "line"
        volume_switch.value = False
        tps_input.value = "2500"
        spike_input.value = "0.12"
        
        await pilot.pause()
        
        # Click the save button
        await pilot.click("#btn-save")
        await pilot.pause()
        
        # Verify Settings screen has closed
        assert app.screen.id != "settings"
        
        # Verify AppConfig changes
        assert app.cfg.theme == "dracula"
        assert app.cfg.chart_type == "line"
        assert app.cfg.show_volume is False
        assert app.cfg.equity_tps == 2500
        assert app.cfg.engine.spike_pct == 0.12
        
        # Verify dynamic changes to app and widgets on the fly
        assert app.theme == "dracula"
        assert price_chart.chart_type == "line"
        assert price_chart2.chart_type == "line"
        assert volume_chart.display is False
        assert volume_chart2.display is False
        assert app._equity.tps == 2500
        assert app.engine.cfg.spike_pct == 0.12
        
        # Exit cleanly
        await pilot.press("q")


@pytest.mark.asyncio
async def test_dynamic_theme_switching_all_themes():
    # Verify dynamic updates for all 7 custom themes on the fly
    app = EntropyApp(AppConfig(enable_crypto=False))
    
    themes_to_test = ["entropy", "dracula", "cyberpunk", "nord", "forest", "monochrome", "sweet"]
    
    async with app.run_test(size=(120, 60)) as pilot:
        for theme_name in themes_to_test:
            # Open Settings
            await pilot.press("s")
            assert app.screen.id == "settings"
            
            settings_screen = app.screen
            theme_select = settings_screen.query_one("#set-theme", Select)
            
            # Switch theme
            theme_select.value = theme_name
            await pilot.pause()
            
            # Save
            await pilot.click("#btn-save")
            await pilot.pause()
            
            # Check if active theme updated on the fly
            assert app.theme == theme_name
            assert app.cfg.theme == theme_name
            
        # Exit cleanly
        await pilot.press("q")


@pytest.mark.asyncio
async def test_settings_risk_profile_change_flow():
    # Initialize app with crypto disabled to prevent networking/websocket calls
    app = EntropyApp(AppConfig(enable_crypto=False))
    
    async with app.run_test(size=(120, 60)) as pilot:
        # Check initial default risk_profile configuration value
        assert app.cfg.risk_profile == "medium"
        
        # Open Settings modal
        await pilot.press("s")
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        assert isinstance(settings_screen, SettingsScreen)
        
        # Find the Select widget for risk profile
        risk_select = settings_screen.query_one("#set-risk", Select)
        assert risk_select.value == "medium"
        
        # Change selection to 'extreme'
        risk_select.value = "extreme"
        await pilot.pause()
        
        # Click the save button to trigger confirmation modal
        await pilot.click("#btn-save")
        await pilot.pause()
        
        # Verify confirmation screen is active
        confirm_screen = app.screen
        assert isinstance(confirm_screen, SettingsConfirmScreen)
        
        # Verify message text
        msg_text = str(confirm_screen.query_one("#confirm-message").render())
        assert msg_text == "Are you sure with that 'Extreme' risk management mode?"
        
        # Click Cancel to return to settings
        await pilot.click("#btn-cancel")
        await pilot.pause()
        
        # Verify settings screen is still active, and config is unchanged
        assert app.screen == settings_screen
        assert app.cfg.risk_profile == "medium"
        
        # Click save again (extreme is still selected)
        await pilot.click("#btn-save")
        await pilot.pause()
        
        # Verify confirmation screen is active again
        confirm_screen = app.screen
        assert isinstance(confirm_screen, SettingsConfirmScreen)
        
        # Click Confirm this time
        await pilot.click("#btn-confirm")
        await pilot.pause()
        
        # Verify both screens closed and settings applied
        assert app.screen.id != "settings"
        assert app.cfg.risk_profile == "extreme"
        assert app.risk_profile == "extreme"
        
        # Open settings again
        await pilot.press("s")
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        risk_select = settings_screen.query_one("#set-risk", Select)
        assert risk_select.value == "extreme"
        
        # Click save without changing (should close instantly without confirm modal)
        await pilot.click("#btn-save")
        await pilot.pause()
        
        assert app.screen.id != "settings"
        
        # Exit cleanly
        await pilot.press("q")


@pytest.mark.asyncio
async def test_settings_risk_profile_no_change_after_toggle_back():
    """Test 1: If selected profile is changed but toggled back to original, it saves immediately without confirmation."""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.cfg.risk_profile == "medium"
        await pilot.press("s")
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        risk_select = settings_screen.query_one("#set-risk", Select)
        
        # Toggle to extreme
        risk_select.value = "extreme"
        await pilot.pause()
        assert risk_select.value == "extreme"
        
        # Toggle back to medium (original profile)
        risk_select.value = "medium"
        await pilot.pause()
        assert risk_select.value == "medium"
        
        # Save
        await pilot.click("#btn-save")
        await pilot.pause()
        
        # Verify it saved immediately without confirmation popup
        assert app.screen.id != "settings"
        assert app.cfg.risk_profile == "medium"
        
        await pilot.press("q")


@pytest.mark.asyncio
async def test_settings_multiple_save_clicks():
    """Test 2: If the user clicks Save multiple times, does it handle screen stack pushes correctly?"""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.cfg.risk_profile == "medium"
        await pilot.press("s")
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        risk_select = settings_screen.query_one("#set-risk", Select)
        
        # Change selection to 'extreme' to trigger confirmation screen on save
        risk_select.value = "extreme"
        await pilot.pause()
        
        # Simulate multiple clicks on save button by posting multiple events
        # before letting the pilot pause/tick the event loop.
        btn_save = settings_screen.query_one("#btn-save", Button)
        
        # Post 3 button pressed events for save
        settings_screen.post_message(Button.Pressed(btn_save))
        settings_screen.post_message(Button.Pressed(btn_save))
        settings_screen.post_message(Button.Pressed(btn_save))
        
        # Give the event loop time to process the events
        await pilot.pause()
        
        # Let's count how many SettingsConfirmScreen instances are in the screen stack
        confirm_screens = [s for s in app.screen_stack if isinstance(s, SettingsConfirmScreen)]
        
        print(f"Number of confirmation screens pushed: {len(confirm_screens)}")
        
        # Assert there is exactly one confirm screen
        assert len(confirm_screens) == 1
        
        # Let's clean up/exit safely
        # Dismiss any confirmation screens first to return to main app screen
        for _ in range(len(confirm_screens)):
            if isinstance(app.screen, SettingsConfirmScreen):
                await pilot.click("#btn-cancel")
                await pilot.pause()
        
        if app.screen.id == "settings":
            await pilot.click("#btn-cancel")
            await pilot.pause()
            
        await pilot.press("q")


@pytest.mark.asyncio
async def test_settings_key_and_click_selection():
    """Test 3: Are the keys used for selection (e.g., arrow keys or pilot clicks) behaving correctly?"""
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        assert app.screen.id == "settings"
        
        settings_screen = app.screen
        risk_select = settings_screen.query_one("#set-risk", Select)
        assert risk_select.value == "medium"
        
        # Test keyboard navigation: focus the Select widget
        risk_select.focus()
        await pilot.pause()
        
        # Let's press enter to open the select overlay
        await pilot.press("enter")
        await pilot.pause()
        
        # Let's select "extreme" using down key
        # In the options menu, medium is selected initially. Extreme is next.
        await pilot.press("down")
        await pilot.pause()
        
        await pilot.press("enter")
        await pilot.pause()
        
        # Let's verify that the value changed
        assert risk_select.value == "extreme"
        
        # Exit settings cleanly
        await pilot.click("#btn-cancel")
        await pilot.pause()
        await pilot.press("q")



