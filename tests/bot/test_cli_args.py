import pytest

from entropy.__main__ import main as main_entry
from entropy.bot.__main__ import _parse_args as bot_parse_args


def test_bot_cli_risk_case_insensitivity():
    # Test bot-specific argparse directly
    parsed1 = bot_parse_args(["--risk", "Frosty"])
    assert parsed1.risk == "frosty"
    
    parsed2 = bot_parse_args(["--risk", "MEDIUM"])
    assert parsed2.risk == "medium"
    
    parsed3 = bot_parse_args(["--risk", "extreme"])
    assert parsed3.risk == "extreme"

def test_main_cli_risk_case_insensitivity(monkeypatch):
    # Test main argparse by mocking the bot run function
    bot_args_received = []
    
    def mock_run_bot(argv):
        nonlocal bot_args_received
        bot_args_received = argv

    monkeypatch.setattr("entropy.__main__.run_bot", mock_run_bot)
    
    # 1. Test Frosty case-insensitivity
    main_entry(["bot", "--risk", "Frosty"])
    assert "--risk" in bot_args_received
    idx1 = bot_args_received.index("--risk")
    assert bot_args_received[idx1 + 1] == "frosty"
    
    # 2. Test MEDIUM case-insensitivity
    main_entry(["bot", "--risk", "MEDIUM"])
    assert "--risk" in bot_args_received
    idx2 = bot_args_received.index("--risk")
    assert bot_args_received[idx2 + 1] == "medium"
    
    # 3. Test extreme case-insensitivity
    main_entry(["bot", "--risk", "extreme"])
    assert "--risk" in bot_args_received
    idx3 = bot_args_received.index("--risk")
    assert bot_args_received[idx3 + 1] == "extreme"

def test_invalid_risk_raises_error():
    with pytest.raises(SystemExit):
        bot_parse_args(["--risk", "invalid_profile"])


def test_cash_zero_forwarded_to_bot_and_reaches_botconfig(monkeypatch):
    """`entropy bot --cash 0` used to be dropped by a truthiness guard
    (`if args.cash:`), silently reverting to the $100k default."""
    from entropy.bot.__main__ import build_config

    received: list[str] = []
    monkeypatch.setattr("entropy.__main__.run_bot", lambda argv: received.extend(argv))
    main_entry(["bot", "--cash", "0"])

    assert "--cash" in received
    idx = received.index("--cash")
    assert received[idx + 1] == "0.0"

    # ...and through the bot's own parser into BotConfig.
    cfg = build_config(bot_parse_args(received))
    assert cfg.starting_cash == 0.0
