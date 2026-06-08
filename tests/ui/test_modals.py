import pytest
from entropy.ui.app import EntropyApp
from entropy.app import AppConfig

@pytest.mark.asyncio
async def test_help_modal_opens_and_closes():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test() as pilot:
        await pilot.press("h")
        assert app.screen.id == "help"
        await pilot.press("escape")
        assert app.screen.id != "help"
