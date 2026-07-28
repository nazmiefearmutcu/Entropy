import pytest

from entropy.engine.timeframe import (
    CHART_INTERVALS,
    DEFAULT_TIMEFRAME,
    FOLLOW_TIMEFRAME,
    TIMEFRAMES,
    TimeframeSpec,
    chart_warmup_interval,
    get_timeframe,
    resolve_chart_interval,
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


# ---- chart intervals (independent of the engine timeframe) ------------------


def test_chart_intervals_are_ordered_and_positive():
    values = list(CHART_INTERVALS.values())
    assert values == sorted(values)
    assert all(v > 0 for v in values)
    assert CHART_INTERVALS["1m"] == _MIN
    assert CHART_INTERVALS["1d"] == _DAY


@pytest.mark.parametrize("name", list(TIMEFRAMES))
def test_follow_timeframe_mirrors_the_timeframe_bar(name):
    """The empty sentinel reproduces the old coupled behaviour exactly."""
    spec = get_timeframe(name)
    assert resolve_chart_interval(FOLLOW_TIMEFRAME, name) == (spec.name, spec.bar_ns)


def test_chart_interval_is_independent_of_the_timeframe():
    """The whole point: 1m candles while the scanner runs on a 15m cadence."""
    chart_name, chart_ns = resolve_chart_interval("1m", "15m")
    assert (chart_name, chart_ns) == ("1m", _MIN)
    assert get_timeframe("15m").bar_ns == 15 * _MIN  # scanner untouched


def test_unknown_chart_interval_degrades_instead_of_raising():
    """This runs on the settings hot-apply path; a typo must not brick the app."""
    assert resolve_chart_interval("banana", "5m") == ("5m", 5 * _MIN)


@pytest.mark.parametrize("interval", list(CHART_INTERVALS))
def test_chart_warmup_interval_never_invents_a_provider(interval):
    crypto = chart_warmup_interval(interval, crypto=True)
    equity = chart_warmup_interval(interval, crypto=False)
    assert crypto in (None, interval)
    assert equity in (None, interval)


def test_sub_minute_equity_candles_have_no_history_source():
    """Yahoo starts at 1m; asking it for 5s bars would 400 or return garbage, so
    the caller is told to fill from the live tape instead."""
    for interval in ("1s", "5s", "15s", "30s"):
        assert chart_warmup_interval(interval, crypto=False) is None
    assert chart_warmup_interval("1s", crypto=True) == "1s"   # Binance serves 1s klines
    assert chart_warmup_interval("1m", crypto=False) == "1m"
