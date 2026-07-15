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


@pytest.mark.asyncio
async def test_console_logs_to_file(tmp_path):
    from textual.app import App, ComposeResult
    log_file = tmp_path / "test_console.log"
    
    class _A(App):
        def compose(self) -> ComposeResult:
            yield AlgoConsole(log_path=str(log_file), id="console")
            
    app = _A()
    async with app.run_test():
        c = app.query_one("#console", AlgoConsole)
        c.push_event(StrategyEvent(EventKind.OPEN_LONG, 1, "SPY", 749.886, running_pnl=0.0))
        c.push_info("Test Info Message [yellow]with tags[/]")
        
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert len(lines) >= 2
    # Verify raw text is written directly
    assert any("OPEN LONG" in l for l in lines)
    assert any("Test Info Message [yellow]with tags[/]" in l for l in lines)
