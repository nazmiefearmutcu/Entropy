import pytest

from textual.widgets import Static

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp


@pytest.mark.asyncio
async def test_help_modal_opens_and_closes():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test() as pilot:
        await pilot.press("h")
        assert app.screen.id == "help"
        await pilot.press("escape")
        assert app.screen.id != "help"


@pytest.mark.asyncio
async def test_settings_modal_opens_and_closes():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test() as pilot:
        await pilot.press("s")
        assert app.screen.id == "settings"
        await pilot.press("escape")
        assert app.screen.id != "settings"


@pytest.mark.asyncio
async def test_bot_tab_dict_vote_mode_renders_note_and_saves_verbatim():
    """A dict vote_mode must not crash the Bot tab (unhashable Select) and a
    save must carry the per-symbol map through verbatim — never flattened."""
    from entropy.bot.config import BotConfig, ConsensusConfig

    app = EntropyApp(AppConfig(enable_crypto=False))
    app.bot_cfg = BotConfig(
        consensus=ConsensusConfig(vote_mode={"BTCUSDT": "trend", "ETHUSDT": "adaptive"})
    )
    async with app.run_test() as pilot:
        await pilot.press("s")
        assert app.screen.id == "settings"
        # the read-only note replaced the crash-prone Select
        assert not list(app.screen.query("#bot-vote-mode"))
        note = app.screen.query_one("#bot-vote-mode-map", Static)
        assert "per-symbol map (2 symbols)" in str(note.render())

        collected = app.screen._collect_bot()
        assert collected.consensus.vote_mode == {
            "BTCUSDT": "trend", "ETHUSDT": "adaptive"
        }
