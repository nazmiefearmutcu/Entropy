import pytest

from entropy.engine.timeframe import (
    DEFAULT_TIMEFRAME,
    TIMEFRAMES,
    TimeframeSpec,
    get_timeframe,
)

_S = 1_000_000_000


def test_default_is_15m():
    assert DEFAULT_TIMEFRAME == "15m"
    assert DEFAULT_TIMEFRAME in TIMEFRAMES


def test_15m_spec_values():
    spec = get_timeframe("15m")
    assert isinstance(spec, TimeframeSpec)
    assert spec.name == "15m"
    assert spec.bar_ns == 900 * _S
    assert spec.window_labels == ("15m", "1h", "4h")
    assert spec.windows_ns == (900 * _S, 3600 * _S, 4 * 3600 * _S)
    assert spec.momentum_horizon_s == 450.0
    assert spec.breadth_window_s == 900
    assert spec.momentum_cooldown_ns == 450 * _S
    assert spec.warmup_bars == 24


def test_every_spec_has_three_ordered_rolling_windows():
    for name, spec in TIMEFRAMES.items():
        assert len(spec.window_labels) == 3, name
        assert len(spec.windows_ns) == 3, name
        assert spec.windows_ns[0] < spec.windows_ns[1] < spec.windows_ns[2], name


def test_get_timeframe_unknown_raises():
    with pytest.raises(KeyError):
        get_timeframe("7m")
