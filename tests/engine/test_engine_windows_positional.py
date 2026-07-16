from entropy.config import EngineConfig
from entropy.engine.engine import _WIN_ORDER, Engine
from entropy.engine.events import WindowName
from entropy.engine.timeframe import get_timeframe

_S = 1_000_000_000


def _engine_15m() -> Engine:
    # The main app runs on 15m; construct that explicitly (a bare Engine() is legacy second-scale).
    return Engine(EngineConfig.from_timeframe(get_timeframe("15m")))


def test_win_order_is_three_positional():
    assert _WIN_ORDER == (WindowName.W0, WindowName.W1, WindowName.W2)


def test_snapshot_counts_keyed_by_display_label():
    eng = _engine_15m()
    ts = 0
    for px in (100.0, 101.0, 102.0, 101.0, 99.0):
        ts += 1 * _S
        eng.on_trade("AAA", px, 1.0, "buy", ts)
    snap = eng.snapshot()
    assert list(snap.breadth.nh_counts.keys()) == ["15m", "1h", "4h"]
    assert list(snap.breadth.nl_counts.keys()) == ["15m", "1h", "4h"]


def test_rolling_window_event_counts_decay_over_time():
    eng = _engine_15m()  # windows 15m / 1h / 4h
    _MIN = 60 * 1_000_000_000
    # AAA seeds (first tick = no events), then rising ticks create new highs in all windows
    eng.on_trade("AAA", 100.0, 1.0, "buy", 1 * _MIN)
    eng.on_trade("AAA", 101.0, 1.0, "buy", 2 * _MIN)
    eng.on_trade("AAA", 102.0, 1.0, "buy", 3 * _MIN)
    s1 = eng.snapshot()
    assert s1.breadth.nh_counts["15m"] >= 1
    assert s1.breadth.nh_counts["4h"] >= 1
    # Advance the clock ~30min via a DIFFERENT symbol (its first tick seeds, emits no events,
    # and does not create any AAA event) so AAA's 15m high-events age out.
    eng.on_trade("BBB", 50.0, 1.0, "buy", 30 * _MIN)
    s2 = eng.snapshot()
    # cutoff for 15m window = 30min - 15min = 15min; AAA's highs at 2min/3min are older → evicted
    assert s2.breadth.nh_counts["15m"] == 0
    # 4h window cutoff = 30min - 240min < 0 → AAA's highs still counted
    assert s2.breadth.nh_counts["4h"] >= 1
