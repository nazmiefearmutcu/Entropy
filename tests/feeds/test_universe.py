import random
from entropy.feeds.equities.universe import UNIVERSE, INDICES, SECTORS, build_params

def test_universe_has_indices_and_many_stocks():
    assert INDICES == ("SPY", "QQQ", "IWM")
    assert set(INDICES).issubset(set(UNIVERSE))
    assert len(UNIVERSE) >= 150
    assert len(set(UNIVERSE)) == len(UNIVERSE)   # no duplicates

def test_build_params_deterministic_and_covers_all():
    p1 = build_params(random.Random(42))
    p2 = build_params(random.Random(42))
    assert set(p1) == set(UNIVERSE)
    assert all(p1[s].s0 == p2[s].s0 for s in UNIVERSE)   # same seed -> same params
    assert all(p1[s].s0 > 0 and p1[s].sigma_bps > 0 for s in UNIVERSE)
