from entropy.config import EngineConfig
from entropy.engine.timeframe import get_timeframe

_S = 1_000_000_000


def test_default_engine_config_is_legacy_second_scale():
    # A bare EngineConfig() stays on the legacy sub-minute cadence (30s/1m/5m,
    # 5s momentum) so bare consumers like the trading bot keep their behavior.
    # The main app's 15m timeframe is expressed via EngineConfig.from_timeframe().
    cfg = EngineConfig()
    assert cfg.windows_ns == {"w0": 30 * _S, "w1": 60 * _S, "w2": 300 * _S}
    assert cfg.window_labels == ("30s", "1m", "5m")
    assert cfg.momentum_horizon_s == 5.0
    assert cfg.breadth_window_s == 30
    assert cfg.momentum_cooldown_ns == 1 * _S


def test_from_timeframe_15m_default_app_timeframe():
    cfg = EngineConfig.from_timeframe(get_timeframe("15m"))
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
