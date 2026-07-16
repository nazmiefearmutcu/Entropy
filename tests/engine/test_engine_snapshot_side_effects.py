# tests/engine/test_engine_snapshot_side_effects.py
"""Candidate 1: snapshot() must be side-effect-free and per-window event deques
must stay bounded even when snapshot() is never called (headless bot runs
Engine.on_trade constantly but rarely/never snapshots)."""
from entropy.engine.engine import Engine

S = 1_000_000_000


def test_reference_snapshot_counts_and_ticker():
    # Reference scenario capturing the CURRENT (correct) snapshot count semantics.
    # Written before the eviction move so the fix can be proven behavior-preserving.
    e = Engine()  # legacy second-scale: 30s / 1m / 5m
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)          # seed, no events
    e.on_trade("AAA", 101.0, 1.0, "buy", 1 * S)      # NH w0/w1/w2 + session
    e.on_trade("AAA", 102.0, 1.0, "buy", 2 * S)      # NH w0/w1/w2 + session
    e.on_trade("AAA", 99.0, 1.0, "sell", 3 * S)      # NL w0/w1/w2 + session
    e.on_trade("BBB", 50.0, 1.0, "buy", 40 * S)      # seed; clock -> 40s, w0 cutoff = 10s
    snap = e.snapshot()
    # AAA's 30s-window events (at 1..3s) have aged out; 1m/5m still hold them.
    assert snap.breadth.nh_counts == {"30s": 0, "1m": 2, "5m": 2}
    assert snap.breadth.nl_counts == {"30s": 0, "1m": 1, "5m": 1}
    ticker = {g.window: g.entries for g in snap.ticker}
    assert ticker["30s"] == ()
    assert ticker["1m"] == (("AAA", 3),)
    assert ticker["5m"] == (("AAA", 3),)
    # nh_count leaderboard semantics: 2 highs x (3 rolling windows + session) = 8
    row = snap.new_highs[0]
    assert row.symbol == "AAA" and row.count == 8


def test_event_deques_bounded_without_snapshot():
    # Headless-bot profile: constant on_trade, snapshot never called. The
    # per-window nh/nl timestamp deques must stay bounded by window contents.
    e = Engine()  # w0=30s, w1=60s, w2=300s
    for i in range(400):
        # strictly rising price: every non-seed trade is a NewHigh in all windows
        e.on_trade("AAA", 100.0 + i, 1.0, "buy", i * S)
    t = e._tapes["AAA"]
    assert len(t.nh_by_win[0]) <= 32     # 30s window -> ~31 in-window stamps
    assert len(t.nh_by_win[1]) <= 62     # 60s window
    assert len(t.nh_by_win[2]) <= 302    # 300s window


def test_prev30s_rate_is_previous_sample_not_current():
    # The snapshot's prev30s_rate must expose the PREVIOUS sample's event rate.
    # The sampling block used to overwrite _prev_event_rate with the current
    # rate before the snapshot read it, so the field always echoed the live rate.
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.on_trade("AAA", 101.0, 1.0, "buy", 1 * S)
    s1 = e.snapshot()
    assert s1.breadth.prev30s_rate == 0.0     # no prior sample yet
    r1 = e.breadth.event_rate()               # the rate sample 1 was taken at
    e.on_trade("AAA", 102.0, 1.0, "buy", 2 * S)
    s2 = e.snapshot()
    assert e.breadth.event_rate() != r1       # rates genuinely differ across samples
    assert s2.breadth.prev30s_rate == r1      # bug: reports the CURRENT rate instead


def test_snapshot_back_to_back_is_idempotent():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.on_trade("AAA", 101.0, 1.0, "buy", 1 * S)
    s1 = e.snapshot()
    s2 = e.snapshot()
    # No new trades between the calls -> the two snapshots must be identical
    # (including the accel flag, which previously flipped to "steady" because
    # snapshot() itself advanced the prev-rate state).
    assert s1 == s2
