import pytest

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.console import AlgoConsole


@pytest.mark.asyncio
async def test_spy_strategy_warm_and_watching_line_on_mount():
    # crypto disabled -> no network; SPY warms from synthesized sim bars.
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.strategy.is_warm
        console = app.query_one("#console", AlgoConsole)
        # warmup INFO + watching [SPY] INFO both pushed at startup.
        assert console.line_count >= 2
        await pilot.press("q")


@pytest.mark.asyncio
async def test_reconnect_noise_pushes_info_line():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test() as pilot:
        await pilot.pause()
        console = app.query_one("#console", AlgoConsole)
        before = console.line_count
        app._feed_status("connecting…")
        await pilot.pause()
        assert console.line_count > before
        await pilot.press("q")
