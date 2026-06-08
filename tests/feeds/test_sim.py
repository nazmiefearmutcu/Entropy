import random
from entropy.feeds.equities.sim import EquitySimulator

def _clock():
    return 1_000_000_000_000_000_000

def test_step_returns_tuple_and_positive_price():
    sim = EquitySimulator(random.Random(1), _clock)
    sym, px, size, side = sim.step_symbol("AAPL")
    assert sym == "AAPL" and px > 0 and size >= 1 and side in ("buy", "sell")

def test_deterministic_same_seed_same_path():
    a = EquitySimulator(random.Random(7), _clock)
    b = EquitySimulator(random.Random(7), _clock)
    seq_a = [a.step_symbol("NVDA")[1] for _ in range(200)]
    seq_b = [b.step_symbol("NVDA")[1] for _ in range(200)]
    assert seq_a == seq_b

def test_spike_injection_changes_state():
    sim = EquitySimulator(random.Random(3), _clock)
    for _ in range(500):
        sim.maybe_inject_events()
    assert len(sim._spike) >= 1   # some symbol got a spike overlay
