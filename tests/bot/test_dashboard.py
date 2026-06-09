import pytest

from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner
from entropy.bot.ui.app import BotDashboard
from entropy.bot.ui.widgets import ModeBanner, RiskBanner


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
    from entropy.bot.risk.profiles import AGGRESSIVE
    b = RiskBanner()
    b.set_profile(AGGRESSIVE)
    assert "AGGRESSIVE" in b.render_text()
    assert b.color == "red"


def test_dashboard_constructs_with_runner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    assert app.runner is bot


@pytest.mark.asyncio
async def test_dashboard_boots_and_shows_banner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="conservative")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one(RiskBanner)
        assert "CONSERVATIVE" in banner.render_text()


@pytest.mark.asyncio
async def test_changing_profile_updates_runner_and_banner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="conservative")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.apply_risk_change("aggressive")  # the post-confirmation callback
        await pilot.pause()
        assert bot.risk.profile.name == "Aggressive"
        assert "AGGRESSIVE" in app.query_one(RiskBanner).render_text()
