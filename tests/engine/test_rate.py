# tests/engine/test_rate.py
import pytest

from entropy.engine.rate import RateMeter

S = 1_000_000_000  # 1s in ns


def test_steady_rate_three_per_sec():
    # 3 events/s for 60s on a 30s window. Retention is the CLOSED interval
    # [now-30s, now], so with whole-second stamps 31 marks (sec 29..59) remain:
    # 93 events over the 30s window. The old exact-3.0 expectation encoded the
    # removed `elapsed + 1.0` fudge, not the true windowed rate.
    m = RateMeter(window_s=30)
    for sec in range(60):
        for _ in range(3):
            m.add(sec * S)
    assert m.rate_per_s() == pytest.approx(93 / 30)


def test_steady_rate_fine_grained_timestamps():
    # With dense (sub-second) stamps the boundary mark is negligible and the
    # honest formula converges on the true rate: 10 events/s.
    m = RateMeter(window_s=30)
    for i in range(601):            # one event every 100ms for 60s
        m.add(i * S // 10)
    assert m.rate_per_s() == pytest.approx(10.0, rel=0.02)


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


def test_warmup_rate_uses_actual_elapsed_span():
    # 10 events over the first 2 seconds of a 30s window is an honest 5/s,
    # not the damped 10/(2+1) = 3.33/s the old fudge produced.
    m = RateMeter(window_s=30)
    m.add(0 * S, 5)
    m.add(2 * S, 5)
    assert m.rate_per_s() == 5.0


def test_subsecond_burst_reads_per_second_not_infinite():
    # All stamps at one instant: elapsed=0 gets floored to 1s instead of
    # dividing by zero / exploding.
    m = RateMeter(window_s=30)
    m.add(0, 10)
    assert m.rate_per_s() == 10.0
