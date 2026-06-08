# tests/engine/test_rate.py
from entropy.engine.rate import RateMeter

S = 1_000_000_000  # 1s in ns

def test_steady_rate_three_per_sec():
    m = RateMeter(window_s=30)
    for sec in range(60):
        for _ in range(3):
            m.add(sec * S)
    assert m.rate_per_s() == 3.0   # last 30s all had 3/s

def test_window_evicts_old_buckets():
    m = RateMeter(window_s=2)
    m.add(0 * S, 5)
    m.add(1 * S, 5)
    m.add(3 * S, 1)   # sec 0 now older than window (3-2=1) -> evicted
    assert m.total == 6            # sec1(5) + sec3(1)

def test_raw_hz_last_second():
    m = RateMeter(window_s=1)
    m.add(10 * S, 4000)
    assert m.rate_per_s() == 4000.0
