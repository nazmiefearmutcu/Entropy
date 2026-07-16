# tests/engine/test_engine_cfg_hot_apply.py
"""Candidate 2: reassigning engine.cfg (the UI's non-timeframe settings path,
ui/app.py _apply_settings) must hot-apply momentum horizon and cooldown — not
leave the construction-time cached scalars and tape spans in place."""
import msgspec

from entropy.engine.engine import Engine
from entropy.engine.events import Spike

S = 1_000_000_000


def _spikes(events):
    return [ev for ev in events if isinstance(ev, Spike)]


def test_horizon_reassign_applies_to_existing_tape():
    e = Engine()  # momentum_horizon_s=5.0
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.on_trade("AAA", 100.0, 1.0, "buy", 1 * S)
    e.on_trade("AAA", 100.0, 1.0, "buy", 2 * S)
    e.cfg = msgspec.structs.replace(e.cfg, momentum_horizon_s=2.0)
    assert e._tapes["AAA"].mom.span_ns == 2 * S     # existing tape re-spanned
    # At t=3s the 2s-horizon anchor (t=1s, 100.0) exists; +1% >= spike_pct 0.40.
    # Under the stale 5s horizon there is no anchor yet and NO event fires.
    evs = _spikes(e.on_trade("AAA", 101.0, 1.0, "buy", 3 * S))
    assert len(evs) == 1
    assert evs[0].horizon_s == 2.0
    assert evs[0].ref_price == 100.0


def test_horizon_reassign_applies_to_new_tape():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.cfg = msgspec.structs.replace(e.cfg, momentum_horizon_s=2.0)
    e.on_trade("BBB", 100.0, 1.0, "buy", 10 * S)
    e.on_trade("BBB", 100.0, 1.0, "buy", 11 * S)
    evs = _spikes(e.on_trade("BBB", 101.0, 1.0, "buy", 13 * S))
    assert len(evs) == 1
    # A tape created AFTER the reassign got the 2s span, but the emitted event
    # previously still carried the stale construction-time horizon_s=5.0.
    assert evs[0].horizon_s == 2.0


def test_cooldown_reassign_applies_to_subsequent_trades():
    e = Engine()  # momentum_cooldown_ns = 1s
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.on_trade("AAA", 100.0, 1.0, "buy", 1 * S)
    first = _spikes(e.on_trade("AAA", 102.0, 1.0, "buy", 6 * S))   # +2% spike at t=6s
    assert len(first) == 1
    e.cfg = msgspec.structs.replace(e.cfg, momentum_cooldown_ns=10 * S)
    # t=8s is 2s after the last spike: allowed under the stale 1s cooldown,
    # suppressed under the newly applied 10s cooldown.
    suppressed = _spikes(e.on_trade("AAA", 103.0, 1.0, "buy", 8 * S))
    assert suppressed == []
    # ...and fires again once the new cooldown has elapsed (17s - 6s = 11s >= 10s).
    later = _spikes(e.on_trade("AAA", 105.0, 1.0, "buy", 17 * S))
    assert len(later) == 1
