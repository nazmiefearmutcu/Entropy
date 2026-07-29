import os
import shutil
import sys
from pathlib import Path

# Ensure Entropy src is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from entropy.__main__ import main as main_entry
from entropy.bot.__main__ import _parse_args, build_config
from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner


def test_defaults():
    print("Testing defaults...")
    
    # 1. Clean up default paths
    default_log = Path("entropy_console.log")
    default_csv = Path("entropy_trades.csv")
    if default_log.exists():
        default_log.unlink()
    if default_csv.exists():
        default_csv.unlink()
        
    # 2. Check defaults in BotConfig
    cfg = BotConfig()
    assert cfg.console_log_path == "entropy_console.log", f"Expected default log path, got {cfg.console_log_path}"
    assert cfg.trade_csv_path == "entropy_trades.csv", f"Expected default csv path, got {cfg.trade_csv_path}"
    
    # 3. Initialize BotRunner, which in turn initializes the Ledger
    run_dir = "runs/test_default_run"
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
        
    bot = BotRunner(cfg, run_dir=run_dir)
    
    # Check that default trade CSV is created with headers
    assert default_csv.exists(), "Default trade CSV was not created!"
    csv_content = default_csv.read_text(encoding="utf-8")
    assert csv_content.startswith("Symbol,Side,Open Price,Close Price"), f"Unexpected header: {csv_content}"
    
    # Record trades
    bot.ledger.record_trade_open("AAPL", "LONG", 150.0)
    bot.ledger.record_trade_close("AAPL", "LONG", 155.0)
    
    csv_lines = default_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(csv_lines) == 2, f"Expected 2 lines, got {len(csv_lines)}"
    assert csv_lines[1] == "AAPL,LONG,150.0,155.0", f"Unexpected trade line: {csv_lines[1]}"
    
    # Check console log writing (simulated via AlgoConsole or just direct verification of BotRunner's cfg)
    # The runner's ledger does not write to console log directly (that is done by AlgoConsole UI widget)
    # But let's verify that the path is set correctly in the runner
    assert bot.ledger.trade_csv_path == "entropy_trades.csv"
    
    # Clean up
    if default_log.exists():
        default_log.unlink()
    if default_csv.exists():
        default_csv.unlink()
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
        
    print("Defaults test passed successfully!")

def test_custom_overrides():
    print("Testing custom overrides...")
    
    custom_log = Path("custom_console.log")
    custom_csv = Path("custom_trades.csv")
    
    if custom_log.exists():
        custom_log.unlink()
    if custom_csv.exists():
        custom_csv.unlink()
        
    cfg = BotConfig(console_log_path=str(custom_log), trade_csv_path=str(custom_csv))
    assert cfg.console_log_path == str(custom_log)
    assert cfg.trade_csv_path == str(custom_csv)
    
    run_dir = "runs/test_custom_run"
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
        
    bot = BotRunner(cfg, run_dir=run_dir)
    
    # Verify custom files are created, and default files are NOT created
    assert custom_csv.exists(), "Custom CSV file was not created!"
    assert not Path("entropy_trades.csv").exists(), "Default trade CSV was incorrectly created!"
    
    # Record trades
    bot.ledger.record_trade_open("MSFT", "SHORT", 350.0)
    bot.ledger.record_trade_close("MSFT", "SHORT", 340.0)
    
    csv_lines = custom_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(csv_lines) == 2
    assert csv_lines[1] == "MSFT,SHORT,350.0,340.0"
    
    # Clean up
    if custom_log.exists():
        custom_log.unlink()
    if custom_csv.exists():
        custom_csv.unlink()
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
        
    print("Custom overrides test passed successfully!")

def test_cli_parsing():
    print("Testing CLI parsing...")
    
    # 1. Test bot subcommand parser directly
    ns = _parse_args(["--console-log", "test_cli_console.log", "--trade-csv", "test_cli_trades.csv"])
    cfg = build_config(ns)
    assert cfg.console_log_path == "test_cli_console.log"
    assert cfg.trade_csv_path == "test_cli_trades.csv"
    
    # 2. Test main entry point command line parsing via mocks
    received_ui = {}
    def mock_run_ui(console_log, trade_csv):
        received_ui["console_log"] = console_log
        received_ui["trade_csv"] = trade_csv
    
    import entropy.__main__
    orig_run_ui = entropy.__main__.run_ui
    entropy.__main__.run_ui = mock_run_ui
    
    try:
        # Global console/trade-csv override
        main_entry(["--console-log", "g_con.log", "--trade-csv", "g_tr.csv", "ui"])
        assert received_ui.get("console_log") == "g_con.log"
        assert received_ui.get("trade_csv") == "g_tr.csv"
        
        # Subcommand console/trade-csv override
        received_ui.clear()
        main_entry(["ui", "--console-log", "ui_con.log", "--trade-csv", "ui_tr.csv"])
        assert received_ui.get("console_log") == "ui_con.log"
        assert received_ui.get("trade_csv") == "ui_tr.csv"
        
        # Global vs Subcommand precedence (subcommand should win)
        received_ui.clear()
        main_entry(["--console-log", "g_con.log", "--trade-csv", "g_tr.csv", "ui", "--console-log", "ui_con.log", "--trade-csv", "ui_tr.csv"])
        assert received_ui.get("console_log") == "ui_con.log"
        assert received_ui.get("trade_csv") == "ui_tr.csv"
    finally:
        entropy.__main__.run_ui = orig_run_ui
        
    print("CLI parsing test passed successfully!")

if __name__ == "__main__":
    test_defaults()
    test_custom_overrides()
    test_cli_parsing()
    print("ALL LOGGING AND CSV PATH OVERRIDE TESTS PASSED!")
