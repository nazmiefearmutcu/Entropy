import pytest

from entropy.ui.app import EntropyApp


@pytest.mark.asyncio
async def test_app_boots_and_has_panels():
    app = EntropyApp(headless=True)
    async with app.run_test() as pilot:
        assert app.query_one("#console") is not None
        assert app.query_one("#status") is not None
        await pilot.press("q")
