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


def test_bot_cli_cost_flags_parse_and_apply():
    """Cost flags parse and override the base config field-by-field."""
    from entropy.bot.__main__ import build_config

    ns = bot_parse_args([
        "--no-cost-aware",
        "--cost-edge-mult", "3",
        "--max-cost-to-stop", "0.25",
        "--spot-fee-bps", "7.5",
        "--spot-slippage-bps", "2.5",
        "--futures-fee-bps", "4",
        "--futures-slippage-bps", "1.5",
        "--equity-fee-bps", "1",
        "--equity-slippage-bps", "0.5",
        "--ignore-saved",
    ])
    assert ns.cost_aware is False
    assert ns.cost_edge_mult == 3.0
    assert ns.max_cost_to_stop == 0.25
    assert ns.spot_fee_bps == 7.5
    assert ns.spot_slippage_bps == 2.5
    assert ns.futures_fee_bps == 4.0
    assert ns.futures_slippage_bps == 1.5
    assert ns.equity_fee_bps == 1.0
    assert ns.equity_slippage_bps == 0.5

    cfg = build_config(ns)
    assert cfg.cost_aware is False
    assert cfg.cost_edge_mult == 3.0
    assert cfg.max_cost_to_stop == 0.25
    assert cfg.market_costs.crypto_spot_fee_bps == 7.5
    assert cfg.market_costs.crypto_spot_slippage_bps == 2.5
    assert cfg.market_costs.crypto_futures_fee_bps == 4.0
    assert cfg.market_costs.crypto_futures_slippage_bps == 1.5
    assert cfg.market_costs.equity_fee_bps == 1.0
    assert cfg.market_costs.equity_slippage_bps == 0.5


def test_bot_cli_cost_aware_flag_positive_and_negative():
    ns = bot_parse_args(["--cost-aware", "--ignore-saved"])
    assert ns.cost_aware is True
    ns = bot_parse_args(["--no-cost-aware", "--ignore-saved"])
    assert ns.cost_aware is False


def test_bot_cli_cost_defaults_leave_base_config_untouched():
    """No cost flags = argparse defaults stay None = base config wins."""
    from entropy.bot.__main__ import build_config
    from entropy.bot.config import MarketCostConfig

    ns = bot_parse_args(["--ignore-saved"])
    assert ns.cost_aware is None
    assert ns.cost_edge_mult is None
    assert ns.max_cost_to_stop is None
    for dest, _field in (
        ("spot_fee_bps", "crypto_spot_fee_bps"),
        ("spot_slippage_bps", "crypto_spot_slippage_bps"),
        ("futures_fee_bps", "crypto_futures_fee_bps"),
        ("futures_slippage_bps", "crypto_futures_slippage_bps"),
        ("equity_fee_bps", "equity_fee_bps"),
        ("equity_slippage_bps", "equity_slippage_bps"),
    ):
        assert getattr(ns, dest) is None

    cfg = build_config(ns)
    assert cfg.cost_aware is True
    assert cfg.cost_edge_mult == 1.0
    assert cfg.max_cost_to_stop == 0.5
    assert cfg.market_costs == MarketCostConfig()
