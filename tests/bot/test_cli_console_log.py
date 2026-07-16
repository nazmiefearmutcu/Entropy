from entropy.__main__ import main as main_entry


def test_console_log_global_only(monkeypatch):
    received = {}
    def mock_run_ui(console_log, trade_csv, equity_source=None):
        received["console_log"] = console_log
    monkeypatch.setattr("entropy.__main__.run_ui", mock_run_ui)
    
    main_entry(["--console-log", "global.log", "ui"])
    assert received.get("console_log") == "global.log"

def test_console_log_subcommand_only(monkeypatch):
    received = {}
    def mock_run_ui(console_log, trade_csv, equity_source=None):
        received["console_log"] = console_log
    monkeypatch.setattr("entropy.__main__.run_ui", mock_run_ui)
    
    main_entry(["ui", "--console-log", "ui.log"])
    assert received.get("console_log") == "ui.log"

def test_console_log_override(monkeypatch):
    received = {}
    def mock_run_ui(console_log, trade_csv, equity_source=None):
        received["console_log"] = console_log
    monkeypatch.setattr("entropy.__main__.run_ui", mock_run_ui)
    
    # Global option is overridden by subcommand option
    main_entry(["--console-log", "global.log", "ui", "--console-log", "ui.log"])
    assert received.get("console_log") == "ui.log"

def test_console_log_bot_override(monkeypatch):
    bot_args_received = []
    def mock_run_bot(argv):
        nonlocal bot_args_received
        bot_args_received = argv
    monkeypatch.setattr("entropy.__main__.run_bot", mock_run_bot)
    
    main_entry(["--console-log", "global.log", "bot", "--console-log", "bot.log"])
    assert "--console-log" in bot_args_received
    idx = bot_args_received.index("--console-log")
    assert bot_args_received[idx + 1] == "bot.log"
