import pytest

from entropy.bot.risk.profiles import EXTREME, FROSTY, MEDIUM, get_profile, make_custom


def test_presets_have_expected_numbers():
    assert FROSTY.per_trade_pct == 1.0
    assert FROSTY.max_concurrent == 2
    assert FROSTY.max_daily_loss_pct == 2.0
    assert FROSTY.min_volatility_pct == 0.25
    assert "0.25% minimum volatility threshold" in FROSTY.description
    
    assert MEDIUM.per_trade_pct == 2.5
    assert MEDIUM.min_volatility_pct == 0.15
    assert "0.15% minimum volatility threshold" in MEDIUM.description
    
    assert EXTREME.max_total_exposure_pct == 40.0
    assert EXTREME.min_volatility_pct == 0.05
    assert "0.05% minimum volatility threshold" in EXTREME.description


def test_every_profile_has_color_and_description():
    for p in (FROSTY, MEDIUM, EXTREME):
        assert p.color in {"cyan", "yellow", "red"}
        assert len(p.description) > 20  # human-readable risk explanation


def test_get_profile_is_case_insensitive():
    assert get_profile("medium") is MEDIUM
    assert get_profile("Extreme") is EXTREME


def test_get_profile_unknown_raises():
    with pytest.raises(KeyError):
        get_profile("nope")


def test_make_custom_overrides_and_is_named_custom():
    c = make_custom(per_trade_pct=3.3, max_concurrent=6)
    assert c.name == "Custom"
    assert c.color == "cyan"
    assert c.per_trade_pct == 3.3
    assert c.max_concurrent == 6
