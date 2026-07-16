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
    assert any("OPEN LONG" in line for line in lines)
    assert any("Test Info Message [yellow]with tags[/]" in line for line in lines)


@pytest.mark.asyncio
async def test_console_survives_write_failure_and_stops_retrying(tmp_path, monkeypatch):
    """A failing mirror file must not break the console, and must not be retried per line."""
    import builtins

    from textual.app import App, ComposeResult

    bad_path = str(tmp_path / "logs" / "console.log")
    attempts = 0
    real_open = builtins.open

    def failing_open(file, *args, **kwargs):
        nonlocal attempts
        if str(file) == bad_path:
            attempts += 1
            raise OSError("disk full")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)

    class _A(App):
        def compose(self) -> ComposeResult:
            yield AlgoConsole(log_path=bad_path, id="console")

    app = _A()
    async with app.run_test():
        c = app.query_one("#console", AlgoConsole)
        c.push_info("first line")
        c.push_info("second line")
        assert c.line_count >= 2  # on-screen console keeps working
    assert attempts == 1, "after the first failure the file mirror must be disabled"
    assert c._log_write_failed is True
