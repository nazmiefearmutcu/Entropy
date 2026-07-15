from __future__ import annotations

import msgspec

from entropy.engine.timeframe import TimeframeSpec, get_timeframe

_DEFAULT_SPEC = get_timeframe("15m")


def _default_windows() -> dict[str, int]:
    return {
        "w0": _DEFAULT_SPEC.windows_ns[0],
        "w1": _DEFAULT_SPEC.windows_ns[1],
        "w2": _DEFAULT_SPEC.windows_ns[2],
    }


class EngineConfig(msgspec.Struct, frozen=True):
    windows_ns: dict[str, int] = msgspec.field(default_factory=_default_windows)
    window_labels: tuple[str, str, str] = _DEFAULT_SPEC.window_labels
    momentum_horizon_s: float = _DEFAULT_SPEC.momentum_horizon_s
    spike_pct: float = 0.40
    snapdrop_pct: float = 0.40
    upmove_pct: float = 0.15
    downmove_pct: float = 0.15
    momentum_cooldown_ns: int = _DEFAULT_SPEC.momentum_cooldown_ns
    new_extreme_strict: bool = True
    breadth_window_s: int = _DEFAULT_SPEC.breadth_window_s
    leaderboard_k: int = 20
    accel_eps: float = 0.10

    @classmethod
    def from_timeframe(cls, spec: TimeframeSpec) -> "EngineConfig":
        return cls(
            windows_ns={"w0": spec.windows_ns[0], "w1": spec.windows_ns[1], "w2": spec.windows_ns[2]},
            window_labels=spec.window_labels,
            momentum_horizon_s=spec.momentum_horizon_s,
            breadth_window_s=spec.breadth_window_s,
            momentum_cooldown_ns=spec.momentum_cooldown_ns,
        )
