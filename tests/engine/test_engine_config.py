from entropy.config import EngineConfig
from entropy.engine.timeframe import get_timeframe

_S = 1_000_000_000


def test_default_engine_config_is_15m():
    cfg = EngineConfig()
    assert cfg.windows_ns == {"w0": 900 * _S, "w1": 3600 * _S, "w2": 4 * 3600 * _S}
    assert cfg.window_labels == ("15m", "1h", "4h")
    assert cfg.momentum_horizon_s == 450.0
    assert cfg.breadth_window_s == 900
    assert cfg.momentum_cooldown_ns == 450 * _S


def test_from_timeframe_1h():
    cfg = EngineConfig.from_timeframe(get_timeframe("1h"))
    assert cfg.window_labels == ("1h", "4h", "1d")
    assert cfg.windows_ns == {"w0": 3600 * _S, "w1": 4 * 3600 * _S, "w2": 86400 * _S}
    assert cfg.breadth_window_s == 3600
    # non-timeframe fields keep their defaults
    assert cfg.spike_pct == 0.40
    assert cfg.leaderboard_k == 20
