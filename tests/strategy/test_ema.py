from entropy.strategy.ema import EmaState, ema_update


def test_ema_seeds_with_first_sample():
    st = EmaState(span=3)
    assert ema_update(st, 10.0) == 10.0
    assert st.count == 1


def test_ema_converges_toward_input():
    st = EmaState(span=2)            # alpha = 2/3
    ema_update(st, 10.0)
    v = ema_update(st, 13.0)         # 10 + 2/3*(13-10) = 12.0
    assert abs(v - 12.0) < 1e-9
