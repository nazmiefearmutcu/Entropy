import pytest

from entropy.strategy.engine import EventKind, StrategyEvent
from entropy.ui.widgets.console import AlgoConsole


@pytest.mark.asyncio
async def test_console_writes_event_line():
    from textual.app import App, ComposeResult
    class _A(App):
        def compose(self) -> ComposeResult:
            yield AlgoConsole(id="console")
    app = _A()
    async with app.run_test():
        c = app.query_one("#console", AlgoConsole)
        c.push_event(StrategyEvent(EventKind.OPEN_LONG, 1, "SPY", 749.886, running_pnl=0.0))
        assert c.line_count >= 1
