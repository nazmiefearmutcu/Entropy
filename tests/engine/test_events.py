from entropy.config import EngineConfig
from entropy.engine.events import EventKind, NewHigh, Spike, WindowName


def test_event_structs_carry_fields():
    e = NewHigh(symbol="X", ts_ns=1, price=10.0, window=WindowName.W0, prev_extreme=9.0)
    assert e.kind == EventKind.NEW_HIGH and e.window == WindowName.W0
    s = Spike(symbol="X", ts_ns=2, price=11.0, pct=0.5, horizon_s=5.0, ref_price=10.0)
    assert s.kind == EventKind.SPIKE and s.pct == 0.5

def test_engine_config_defaults():
    c = EngineConfig()
    assert c.spike_pct == 0.40 and c.upmove_pct == 0.15
    assert c.windows_ns["w0"] == 30_000_000_000 and "session" not in c.windows_ns
    assert c.new_extreme_strict is True and c.leaderboard_k == 20
