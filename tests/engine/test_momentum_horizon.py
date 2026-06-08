# tests/engine/test_momentum_horizon.py
from entropy.engine.windows import MomentumHorizon

S = 1_000_000_000


def test_reference_price_anchor():
    h = MomentumHorizon(span_ns=5 * S)
    h.push(0 * S, 100.0)
    h.push(1 * S, 100.5)
    h.push(2 * S, 101.0)
    ref = h.push(6 * S, 108.0)   # cutoff = 1s; keep anchor at/older than cutoff
    assert ref == 100.5
    pct = (108.0 - ref) / ref * 100
    assert abs(pct - 7.4626865) < 1e-4
