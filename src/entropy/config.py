from __future__ import annotations

import msgspec


def _default_windows() -> dict[str, int]:
    return {
        "30s": 30_000_000_000,
        "1m": 60_000_000_000,
        "5m": 300_000_000_000,
        "20m": 1_200_000_000_000,
    }


class EngineConfig(msgspec.Struct, frozen=True):
    windows_ns: dict[str, int] = msgspec.field(default_factory=_default_windows)
    momentum_horizon_s: float = 5.0
    spike_pct: float = 0.40
    snapdrop_pct: float = 0.40
    upmove_pct: float = 0.15
    downmove_pct: float = 0.15
    momentum_cooldown_ns: int = 1_000_000_000
    new_extreme_strict: bool = True
    breadth_window_s: int = 30
    leaderboard_k: int = 20
    accel_eps: float = 0.10
