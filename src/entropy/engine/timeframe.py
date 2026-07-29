from __future__ import annotations

import msgspec

_S = 1_000_000_000
_MIN = 60 * _S
_HOUR = 3600 * _S
_DAY = 24 * _HOUR


class TimeframeSpec(msgspec.Struct, frozen=True):
    """Timeframe-derived engine parameters for a single timeframe."""

    name: str
    bar_ns: int
    window_labels: tuple[str, str, str]
    windows_ns: tuple[int, int, int]
    momentum_horizon_s: float
    breadth_window_s: int
    momentum_cooldown_ns: int
    warmup_bars: int = 24


def _spec(
    name: str,
    bar_ns: int,
    labels: tuple[str, str, str],
    spans: tuple[int, int, int],
    horizon_s: float,
    breadth_s: int,
    cooldown_s: float,
) -> TimeframeSpec:
    return TimeframeSpec(
        name=name,
        bar_ns=bar_ns,
        window_labels=labels,
        windows_ns=spans,
        momentum_horizon_s=horizon_s,
        breadth_window_s=breadth_s,
        momentum_cooldown_ns=int(cooldown_s * _S),
    )


TIMEFRAMES: dict[str, TimeframeSpec] = {
    "1m": _spec(
        "1m",
        bar_ns=_MIN,
        labels=("1m", "5m", "15m"),
        spans=(_MIN, 5 * _MIN, 15 * _MIN),
        horizon_s=30.0,
        breadth_s=60,
        cooldown_s=30.0,
    ),
    "5m": _spec(
        "5m",
        bar_ns=5 * _MIN,
        labels=("5m", "15m", "1h"),
        spans=(5 * _MIN, 15 * _MIN, _HOUR),
        horizon_s=150.0,
        breadth_s=300,
        cooldown_s=150.0,
    ),
    "15m": _spec(
        "15m",
        bar_ns=15 * _MIN,
        labels=("15m", "1h", "4h"),
        spans=(15 * _MIN, _HOUR, 4 * _HOUR),
        horizon_s=450.0,
        breadth_s=900,
        cooldown_s=450.0,
    ),
    "1h": _spec(
        "1h",
        bar_ns=_HOUR,
        labels=("1h", "4h", "1d"),
        spans=(_HOUR, 4 * _HOUR, _DAY),
        horizon_s=1800.0,
        breadth_s=3600,
        cooldown_s=1800.0,
    ),
    "4h": _spec(
        "4h",
        bar_ns=4 * _HOUR,
        labels=("4h", "12h", "1d"),
        spans=(4 * _HOUR, 12 * _HOUR, _DAY),
        horizon_s=7200.0,
        breadth_s=14400,
        cooldown_s=7200.0,
    ),
}

DEFAULT_TIMEFRAME = "15m"

# --- chart candle intervals -------------------------------------------------
#
# The CHART's candle width is deliberately NOT the same knob as the engine
# timeframe. The timeframe drives the scanner (rolling high/low windows,
# momentum horizon, breadth window); the chart interval only decides how wide
# one candle is. Coupling them meant you could never look at 1m candles while
# the scanner ran on a 15m cadence — which is the normal way to trade.
#
# Every entry is a Binance-style interval string so it can be handed straight
# to the kline warmup; ``FOLLOW_TIMEFRAME`` ("") keeps the legacy behaviour of
# mirroring the engine timeframe.
FOLLOW_TIMEFRAME = ""

CHART_INTERVALS: dict[str, int] = {
    "1s": _S,
    "5s": 5 * _S,
    "15s": 15 * _S,
    "30s": 30 * _S,
    "1m": _MIN,
    "3m": 3 * _MIN,
    "5m": 5 * _MIN,
    "15m": 15 * _MIN,
    "30m": 30 * _MIN,
    "1h": _HOUR,
    "4h": 4 * _HOUR,
    "1d": _DAY,
}

# Intervals a remote history provider can actually serve. Sub-minute candles
# build from the live tape only (Binance offers 1s klines; Yahoo starts at 1m),
# so warmup is skipped rather than attempted with a bogus interval.
_KLINE_INTERVALS = frozenset({"1s", "1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"})
_YAHOO_INTERVALS = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d"})


def get_timeframe(name: str) -> TimeframeSpec:
    if name not in TIMEFRAMES:
        raise KeyError(f"Unknown timeframe {name!r}; choose from {sorted(TIMEFRAMES)}.")
    return TIMEFRAMES[name]


def resolve_chart_interval(chart_interval: str, timeframe: str) -> tuple[str, int]:
    """(name, bar_ns) for the chart's candles.

    ``chart_interval`` empty (``FOLLOW_TIMEFRAME``) or unknown falls back to the
    engine timeframe's bar — an unrecognized string must degrade, never raise,
    because this runs on the settings hot-apply path.
    """
    bar_ns = CHART_INTERVALS.get(chart_interval)
    if bar_ns is None:
        spec = get_timeframe(timeframe)
        return spec.name, spec.bar_ns
    return chart_interval, bar_ns


def chart_warmup_interval(interval: str, *, crypto: bool) -> str | None:
    """Kline/bar interval to seed a chart with, or None when no provider serves it.

    Sub-minute equity candles have no history source; sub-second crypto has none
    either. Returning None tells the caller to let the chart fill from live ticks
    instead of firing a request that would 400 or silently return garbage.
    """
    allowed = _KLINE_INTERVALS if crypto else _YAHOO_INTERVALS
    return interval if interval in allowed else None
