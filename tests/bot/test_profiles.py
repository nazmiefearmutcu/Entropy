import pytest

from entropy.bot.risk.profiles import AGGRESSIVE, BALANCED, CONSERVATIVE, get_profile, make_custom


def test_presets_have_expected_numbers():
    assert CONSERVATIVE.per_trade_pct == 1.0
    assert CONSERVATIVE.max_concurrent == 2
    assert CONSERVATIVE.max_daily_loss_pct == 2.0
    assert BALANCED.per_trade_pct == 2.5
    assert AGGRESSIVE.max_total_exposure_pct == 40.0


def test_every_profile_has_color_and_description():
    for p in (CONSERVATIVE, BALANCED, AGGRESSIVE):
        assert p.color in {"green", "yellow", "red"}
        assert len(p.description) > 20  # human-readable risk explanation


def test_get_profile_is_case_insensitive():
    assert get_profile("balanced") is BALANCED
    assert get_profile("Aggressive") is AGGRESSIVE


def test_get_profile_unknown_raises():
    with pytest.raises(KeyError):
        get_profile("nope")


def test_make_custom_overrides_and_is_named_custom():
    c = make_custom(per_trade_pct=3.3, max_concurrent=6)
    assert c.name == "Custom"
    assert c.color == "cyan"
    assert c.per_trade_pct == 3.3
    assert c.max_concurrent == 6
