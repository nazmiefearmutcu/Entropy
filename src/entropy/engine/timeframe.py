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
    "1m": _spec("1m", bar_ns=_MIN, labels=("1m", "5m", "15m"), spans=(_MIN, 5 * _MIN, 15 * _MIN), horizon_s=30.0, breadth_s=60, cooldown_s=30.0),
    "5m": _spec("5m", bar_ns=5 * _MIN, labels=("5m", "15m", "1h"), spans=(5 * _MIN, 15 * _MIN, _HOUR), horizon_s=150.0, breadth_s=300, cooldown_s=150.0),
    "15m": _spec("15m", bar_ns=15 * _MIN, labels=("15m", "1h", "4h"), spans=(15 * _MIN, _HOUR, 4 * _HOUR), horizon_s=450.0, breadth_s=900, cooldown_s=450.0),
    "1h": _spec("1h", bar_ns=_HOUR, labels=("1h", "4h", "1d"), spans=(_HOUR, 4 * _HOUR, _DAY), horizon_s=1800.0, breadth_s=3600, cooldown_s=1800.0),
    "4h": _spec("4h", bar_ns=4 * _HOUR, labels=("4h", "12h", "1d"), spans=(4 * _HOUR, 12 * _HOUR, _DAY), horizon_s=7200.0, breadth_s=14400, cooldown_s=7200.0),
}

DEFAULT_TIMEFRAME = "15m"


def get_timeframe(name: str) -> TimeframeSpec:
    if name not in TIMEFRAMES:
        raise KeyError(f"Unknown timeframe {name!r}; choose from {sorted(TIMEFRAMES)}.")
    return TIMEFRAMES[name]
