# tests/engine/test_engine_perf.py
import time

from entropy.config import EngineConfig
from entropy.engine.engine import Engine
from entropy.engine.timeframe import get_timeframe
from entropy.feeds.equities.universe import UNIVERSE


def test_engine_throughput():
    e = Engine()
    syms = UNIVERSE
    n = 200_000
    base = 1_000_000_000_000
    t0 = time.perf_counter()
    for i in range(n):
        s = syms[i % len(syms)]
        e.on_trade(s, 100.0 + (i % 17) * 0.1, 10.0, "buy" if i & 1 else "sell", base + i * 1000)
    dt = time.perf_counter() - t0
    rate = n / dt
    assert rate > 100_000, f"engine too slow: {rate:.0f} ticks/s"


def test_snapshot_speed_with_populated_windows():
    # Guards the UI's 10 Hz sample_snapshot path: snapshot() must stay ~O(symbols)
    # even in a sustained-trend regime that fills every per-window event deque.
    # (A regression that walked all in-window stamps per snapshot measured
    # ~3.3ms/snapshot on this scenario vs ~0.1ms for evict+len counting.)
    e = Engine(EngineConfig.from_timeframe(get_timeframe("15m")))  # w2 = 4h
    syms = UNIVERSE[:140]
    n = len(syms)
    base = 1_000_000_000_000
    dt_ns = 71_000_000  # ~71ms apart -> 126k trades span ~2.5h, all inside w2
    for i in range(126_000):
        # strictly rising tape: every non-seed trade is a NewHigh in all windows
        e.on_trade(syms[i % n], 100.0 + i * 0.01, 10.0,
                   "buy" if i & 1 else "sell", base + i * dt_ns)
    e.snapshot()  # warm-up: one-time catch-up eviction + accel memo
    t0 = time.perf_counter()
    snaps = 50
    for _ in range(snaps):
        e.snapshot()
    per_ms = (time.perf_counter() - t0) * 1000 / snaps
    # generous vs the ~0.1ms measured cost so parallel test load can't flake it,
    # but far below the ~3.3ms of the O(in-window stamps) regression
    assert per_ms < 2.0, f"snapshot too slow: {per_ms:.3f} ms"
