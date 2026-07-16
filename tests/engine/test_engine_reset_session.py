# tests/engine/test_engine_reset_session.py
"""Candidate 3: reset_session() must not leak pre-reset rolling-window /
momentum state into the new session — post-reset trades must behave exactly
like a truly fresh engine."""
from entropy.engine.engine import Engine

S = 1_000_000_000


def test_first_trade_after_reset_seeds_without_events():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.on_trade("AAA", 200.0, 1.0, "buy", 1 * S)
    e.reset_session()
    assert e.on_trade("AAA", 100.0, 1.0, "buy", 3 * S) == []


def test_reset_matches_truly_fresh_engine():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.on_trade("AAA", 200.0, 1.0, "buy", 1 * S)      # session/rolling high 200
    e.on_trade("AAA", 50.0, 1.0, "sell", 2 * S)      # rolling low 50
    e.reset_session()

    fresh = Engine()
    post_reset = [("AAA", 100.0, 1.0, "buy", 3 * S), ("AAA", 150.0, 1.0, "buy", 4 * S)]
    got = [e.on_trade(*t) for t in post_reset]
    want = [fresh.on_trade(*t) for t in post_reset]
    # Before the fix the pre-reset 200-high stayed inside the MonotonicExtreme
    # windows, so 150 was NOT a rolling new high after reset (only a session one).
    assert got == want
    nh = [ev for ev in got[1] if type(ev).__name__ == "NewHigh"]
    assert len(nh) == 4  # w0 + w1 + w2 + session, exactly like a fresh engine
