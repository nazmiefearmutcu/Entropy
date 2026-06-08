# tests/engine/test_engine_perf.py
import time

from entropy.engine.engine import Engine
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
