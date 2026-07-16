import pytest

from entropy.engine.timeframe import (
    DEFAULT_TIMEFRAME,
    TIMEFRAMES,
    TimeframeSpec,
    get_timeframe,
)

_S = 1_000_000_000
_MIN = 60 * _S
_HOUR = 3600 * _S
_DAY = 24 * _HOUR


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


def test_registry_key_matches_spec_name():
    for key, spec in TIMEFRAMES.items():
        assert spec.name == key


_EXPECTED = {
    "1m": (
        1 * _MIN,
        ("1m", "5m", "15m"),
        (1 * _MIN, 5 * _MIN, 15 * _MIN),
        30.0,
        60,
        30 * _S,
    ),
    "5m": (
        5 * _MIN,
        ("5m", "15m", "1h"),
        (5 * _MIN, 15 * _MIN, 1 * _HOUR),
        150.0,
        300,
        150 * _S,
    ),
    "15m": (
        15 * _MIN,
        ("15m", "1h", "4h"),
        (15 * _MIN, 1 * _HOUR, 4 * _HOUR),
        450.0,
        900,
        450 * _S,
    ),
    "1h": (
        1 * _HOUR,
        ("1h", "4h", "1d"),
        (1 * _HOUR, 4 * _HOUR, 1 * _DAY),
        1800.0,
        3600,
        1800 * _S,
    ),
    "4h": (
        4 * _HOUR,
        ("4h", "12h", "1d"),
        (4 * _HOUR, 12 * _HOUR, 1 * _DAY),
        7200.0,
        14400,
        7200 * _S,
    ),
}


@pytest.mark.parametrize("name", list(_EXPECTED))
def test_all_specs_exact_values(name):
    bar, labels, spans, horizon, breadth, cooldown = _EXPECTED[name]
    spec = get_timeframe(name)
    assert spec.bar_ns == bar
    assert spec.window_labels == labels
    assert spec.windows_ns == spans
    assert spec.momentum_horizon_s == horizon
    assert spec.breadth_window_s == breadth
    assert spec.momentum_cooldown_ns == cooldown
    assert spec.warmup_bars == 24
