from __future__ import annotations

import msgspec

from entropy.engine.timeframe import TimeframeSpec

_S = 1_000_000_000


def _windows_dict(spec: TimeframeSpec) -> dict[str, int]:
    return {"w0": spec.windows_ns[0], "w1": spec.windows_ns[1], "w2": spec.windows_ns[2]}


def _default_windows() -> dict[str, int]:
    # Legacy second-scale defaults (30s / 1m / 5m). A bare EngineConfig() is the
    # neutral default used by consumers that are NOT the timeframe-driven main UI —
    # notably the trading bot (`entropy.bot.runner` builds `Engine()`), which relies
    # on the original sub-minute momentum cadence. The main app never uses these
    # defaults directly: it builds its engine via `EngineConfig.from_timeframe(...)`.
    return {"w0": 30 * _S, "w1": 60 * _S, "w2": 300 * _S}


class EngineConfig(msgspec.Struct, frozen=True):
    windows_ns: dict[str, int] = msgspec.field(default_factory=_default_windows)
    window_labels: tuple[str, str, str] = ("30s", "1m", "5m")
    momentum_horizon_s: float = 5.0
    spike_pct: float = 0.40
    snapdrop_pct: float = 0.40
    upmove_pct: float = 0.15
    downmove_pct: float = 0.15
    momentum_cooldown_ns: int = 1 * _S
    new_extreme_strict: bool = True
    breadth_window_s: int = 30
    leaderboard_k: int = 20
    accel_eps: float = 0.10

    @classmethod
    def from_timeframe(cls, spec: TimeframeSpec) -> EngineConfig:
        """Build an engine config whose windows/scalars come from a timeframe spec.

        This is how the main app expresses its selected timeframe (default 15m);
        the bare-default constructor stays on the legacy second-scale cadence.
        """
        return cls(
            windows_ns=_windows_dict(spec),
            window_labels=spec.window_labels,
            momentum_horizon_s=spec.momentum_horizon_s,
            breadth_window_s=spec.breadth_window_s,
            momentum_cooldown_ns=spec.momentum_cooldown_ns,
        )
