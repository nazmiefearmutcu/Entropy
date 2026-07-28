import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rich.markup import escape
from textual.app import App, ComposeResult

from entropy.__main__ import main as main_entry
from entropy.strategy.engine import EventKind, StrategyEvent
from entropy.strategy.format import render_event
from entropy.ui.widgets.console import AlgoConsole


async def test_bracketed_logging():
    print("--- Testing Bracketed Ticker Symbols Logging ---")
    log_file = Path("test_challenger_console.log")
    if log_file.exists():
        log_file.unlink()
        
    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield AlgoConsole(log_path=str(log_file), id="console")
            
    app = TestApp()
    async with app.run_test() as pilot:
        c = app.query_one("#console", AlgoConsole)
        
        # 1. Info event with bracketed symbol
        # StrategyEvent for INFO uses (e.text or f"watching [{e.symbol}]")
        e1 = StrategyEvent(EventKind.INFO, 1, "[SPY]", 100.0, text=None)
        c.push_event(e1)
        
        # 2. Info event with normal symbol (leads to watching [SPY] if symbol is SPY)
        e2 = StrategyEvent(EventKind.INFO, 2, "SPY", 100.0, text=None)
        c.push_event(e2)
        
        # 3. Direct push_info with bracketed crypto symbol
        c.push_info("watching [binance-spot:BTCUSDT]")
        
        # 4. Info event with crypto symbol
        e3 = StrategyEvent(EventKind.INFO, 3, "binance-spot:BTCUSDT", 50000.0, text=None)
        c.push_event(e3)
        
        # Wait a moment for rendering and write
        await pilot.pause(0.1)
        
        # Check RichLog internal lines/render
        # Let's inspect what lines was written
        lines = [line.text for line in c.lines]
        print("Console lines written:")
        for line in lines:
            print(f"  Line: {repr(line)}")
            
    # Read log file
    assert log_file.exists(), "Log file was not created!"
    log_content = log_file.read_text(encoding="utf-8")
    log_lines = log_content.strip().splitlines()
    print("Log file content:")
    for line in log_lines:
        print(f"  Log: {repr(line)}")
        
    # Check that brackets are fully intact in log file
    assert any("watching [[SPY]]" in l for l in log_lines), "Expected watching [[SPY]] (nested brackets) in log file"
    assert any("watching [SPY]" in l for l in log_lines), "Expected watching [SPY] in log file"
    assert any("watching [binance-spot:BTCUSDT]" in l for l in log_lines), "Expected watching [binance-spot:BTCUSDT] in log file"
    
    # Check that brackets are fully intact in console lines
    # RichLog stores lines as Strip objects. Let's make sure they contain the symbols.
    # Note: text in lines contains unescaped text (i.e. brackets intact)
    assert any("watching [[SPY]]" in l for l in lines)
    assert any("watching [SPY]" in l for l in lines)
    assert any("watching [binance-spot:BTCUSDT]" in l for l in lines)
    
    log_file.unlink()
    print("✔ Bracketed logging check PASSED.")

def test_cli_overrides():
    print("--- Testing CLI console-log Overrides ---")
    import entropy.__main__
    
    received_ui = {}
    def mock_run_ui(console_log, trade_csv):
        received_ui["console_log"] = console_log
    entropy.__main__.run_ui = mock_run_ui
    
    received_bot = []
    def mock_run_bot(argv):
        nonlocal received_bot
        received_bot = argv
    entropy.__main__.run_bot = mock_run_bot
    
    # Test 1: Global only
    main_entry(["--console-log", "global.log", "ui"])
    assert received_ui.get("console_log") == "global.log", f"Expected global.log, got {received_ui.get('console_log')}"
    
    # Test 2: Subcommand only
    received_ui.clear()
    main_entry(["ui", "--console-log", "ui.log"])
    assert received_ui.get("console_log") == "ui.log", f"Expected ui.log, got {received_ui.get('console_log')}"
    
    # Test 3: Override global with subcommand (ui)
    received_ui.clear()
    main_entry(["--console-log", "global.log", "ui", "--console-log", "ui.log"])
    assert received_ui.get("console_log") == "ui.log", f"Expected ui.log, got {received_ui.get('console_log')}"
    
    # Test 4: Override global with subcommand (bot)
    received_bot = []
    main_entry(["--console-log", "global.log", "bot", "--console-log", "bot.log"])
    assert "--console-log" in received_bot
    idx = received_bot.index("--console-log")
    assert received_bot[idx + 1] == "bot.log", f"Expected bot.log, got {received_bot[idx + 1]}"
    
    print("✔ CLI overrides check PASSED.")

async def main():
    await test_bracketed_logging()
    test_cli_overrides()
    print("All Challenger verifications completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
