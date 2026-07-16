import pytest
from textual.widgets import Select, Static

from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner
from entropy.bot.ui.app import BotDashboard
from entropy.bot.ui.widgets import ModeBanner, RiskBanner
from entropy.bot.ui.confirm import BotSettingsScreen, ConfirmRiskScreen


def test_mode_banner_labels_paper_and_live():
    paper = ModeBanner()
    paper.set_mode("paper")
    assert "PAPER" in str(paper.banner_text())
    live = ModeBanner()
    live.set_mode("live")
    assert "LIVE" in str(live.banner_text())


@pytest.mark.asyncio
async def test_dashboard_shows_paper_mode_banner(tmp_path):
    cfg = BotConfig(mode="paper", enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "PAPER" in str(app.query_one(ModeBanner).banner_text())


def test_risk_banner_text_and_color():
    from entropy.bot.risk.profiles import EXTREME
    b = RiskBanner()
    b.set_profile(EXTREME)
    assert "EXTREME" in b.render_text()
    assert b.color == "red"


def test_dashboard_constructs_with_runner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    assert app.runner is bot


@pytest.mark.asyncio
async def test_dashboard_boots_and_shows_banner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one(RiskBanner)
        assert "FROSTY" in banner.render_text()


@pytest.mark.asyncio
async def test_changing_profile_updates_runner_and_banner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.apply_risk_change("extreme")  # the post-confirmation callback
        await pilot.pause()
        assert bot.risk.profile.name == "Extreme"
        assert "EXTREME" in app.query_one(RiskBanner).render_text()


@pytest.mark.asyncio
async def test_dashboard_settings_modal_flow_no_change(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert bot.risk.profile.name.lower() == "frosty"
        
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)
        
        select = app.screen.query_one(Select)
        assert select.value == "frosty"
        
        options = select._options
        assert len(options) == 3
        assert options[0][1] == "frosty"
        assert options[1][1] == "medium"
        assert options[2][1] == "extreme"
        
        await pilot.click("#btn-save")
        await pilot.pause()
        
        assert not isinstance(app.screen, BotSettingsScreen)
        assert not isinstance(app.screen, ConfirmRiskScreen)
        assert bot.risk.profile.name.lower() == "frosty"


@pytest.mark.asyncio
async def test_dashboard_settings_modal_flow_with_change_and_confirm(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)
        
        select = app.screen.query_one(Select)
        select.value = "extreme"
        await pilot.pause()
        
        await pilot.click("#btn-save")
        await pilot.pause()
        
        assert isinstance(app.screen, ConfirmRiskScreen)
        
        msg_text = str(app.screen.query_one(Static).render())
        assert msg_text == "Are you sure with that 'Extreme' risk management mode?"
        
        await pilot.click("#confirm")
        await pilot.pause()
        
        assert not isinstance(app.screen, ConfirmRiskScreen)
        assert not isinstance(app.screen, BotSettingsScreen)
        assert bot.risk.profile.name == "Extreme"


@pytest.mark.asyncio
async def test_dashboard_settings_modal_flow_with_change_and_cancel(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)
        
        select = app.screen.query_one(Select)
        select.value = "extreme"
        await pilot.pause()
        
        await pilot.click("#btn-save")
        await pilot.pause()
        
        assert isinstance(app.screen, ConfirmRiskScreen)
        
        await pilot.click("#cancel")
        await pilot.pause()
        
        assert isinstance(app.screen, BotSettingsScreen)
        assert bot.risk.profile.name == "Frosty"
        
        await pilot.click("#btn-cancel")
        await pilot.pause()
        
        assert not isinstance(app.screen, BotSettingsScreen)
        assert bot.risk.profile.name == "Frosty"


@pytest.mark.asyncio
async def test_dashboard_keys_1_2_3_removed(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="frosty")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        
        await pilot.press("2")
        await pilot.pause()
        
        assert bot.risk.profile.name == "Frosty"
        assert not isinstance(app.screen, ConfirmRiskScreen)
        
        await pilot.press("3")
        await pilot.pause()
        
        assert bot.risk.profile.name == "Frosty"
        assert not isinstance(app.screen, ConfirmRiskScreen)

