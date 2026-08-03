import pytest
from textual.widgets import Input, Label, Select, Static

from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner
from entropy.bot.ui.app import BotDashboard
from entropy.bot.ui.confirm import BotSettingsScreen, ConfirmRiskScreen
from entropy.bot.ui.widgets import ModeBanner, RiskBanner, TradeLog


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
async def test_apply_risk_change_partial_dead_warns_but_applies(tmp_path):
    # Frosty + default costs: crypto spot (0.52) exceeds max_cost_to_stop but
    # equities/futures still trade -> the switch applies with a warning.
    cfg = BotConfig(risk_profile="medium")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.apply_risk_change("frosty")
        await pilot.pause()
        assert bot.risk.profile.name == "Frosty"
        rendered = "\n".join(
            str(line) for line in app.query_one(TradeLog).lines
        )
        assert "risk profile changed -> Frosty" in rendered
        assert "warning:" in rendered and "crypto spot" in rendered


@pytest.mark.asyncio
async def test_settings_modal_frosty_change_warns_but_applies(tmp_path):
    cfg = BotConfig(risk_profile="medium")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        select = app.screen.query_one("#risk-select", Select)
        select.value = "frosty"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRiskScreen)
        await pilot.click("#confirm")
        await pilot.pause()
        assert not isinstance(app.screen, BotSettingsScreen)
        assert bot.risk.profile.name == "Frosty"
        rendered = "\n".join(str(line) for line in app.query_one(TradeLog).lines)
        assert "warning:" in rendered and "crypto spot" in rendered


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
        
        select = app.screen.query_one("#risk-select", Select)
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
        
        select = app.screen.query_one("#risk-select", Select)
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
        
        select = app.screen.query_one("#risk-select", Select)
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


@pytest.mark.asyncio
async def test_dashboard_settings_modal_cost_aware_toggle_applies(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)

        cost_select = app.screen.query_one("#cost-aware-select", Select)
        assert cost_select.value == "on"
        cost_select.value = "off"
        await pilot.pause()

        await pilot.click("#btn-save")
        await pilot.pause()

        assert bot.config.cost_aware is False
        assert not isinstance(app.screen, BotSettingsScreen)


@pytest.mark.asyncio
async def test_dashboard_settings_modal_cost_numbers_apply(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)

        app.screen.query_one("#cost-edge-mult", Input).value = "3.5"
        app.screen.query_one("#max-cost-to-stop", Input).value = "0.4"
        await pilot.pause()

        await pilot.click("#btn-save")
        await pilot.pause()

        assert bot.config.cost_edge_mult == 3.5
        assert bot.config.max_cost_to_stop == 0.4
        assert not isinstance(app.screen, BotSettingsScreen)


@pytest.mark.asyncio
async def test_dashboard_settings_modal_invalid_cost_number_not_applied(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)

        app.screen.query_one("#cost-edge-mult", Input).value = "not-a-number"
        await pilot.pause()

        await pilot.click("#btn-save")
        await pilot.pause()

        assert isinstance(app.screen, BotSettingsScreen)
        assert bot.config.cost_edge_mult == 1.0
        error = app.screen.query_one("#settings-error", Label)
        assert "must be numbers" in str(error.render())


@pytest.mark.asyncio
async def test_dashboard_settings_modal_invalid_cost_value_shows_problem(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, BotSettingsScreen)

        app.screen.query_one("#max-cost-to-stop", Input).value = "0"
        await pilot.pause()

        await pilot.click("#btn-save")
        await pilot.pause()

        assert isinstance(app.screen, BotSettingsScreen)
        assert bot.config.max_cost_to_stop == 0.5
        error = app.screen.query_one("#settings-error", Label)
        assert "max cost-to-stop" in str(error.render())
