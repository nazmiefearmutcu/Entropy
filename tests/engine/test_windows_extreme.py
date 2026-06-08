# tests/engine/test_windows_extreme.py
from entropy.engine.windows import MonotonicExtreme, SessionExtreme


def test_rolling_max_new_high_trace():
    # span=100ns; verified expected new-high flags: T,T,F,F,T
    m = MonotonicExtreme(span_ns=100, kind=+1)
    seq = [(0, 10.0), (50, 12.0), (80, 11.0), (120, 9.0), (160, 13.0)]
    flags = [m.step(ts, px) for ts, px in seq]
    assert flags == [True, True, False, False, True]

def test_equal_price_is_not_new_high_strict():
    m = MonotonicExtreme(span_ns=1000, kind=+1)
    assert m.step(0, 10.0) is True      # first
    assert m.step(1, 10.0) is False     # equal -> not new (STRICT >)

def test_rolling_min_new_low():
    m = MonotonicExtreme(span_ns=100, kind=-1)
    seq = [(0, 10.0), (50, 8.0), (80, 9.0), (160, 11.0)]
    flags = [m.step(ts, px) for ts, px in seq]
    # t0 first->True; t50 8<10 ->True; t80 9 not< min(8) ->False;
    # t160 cutoff=60 evicts 8@50 and 10@0; min now 9@80 -> 11 not<9 ->False
    assert flags == [True, True, False, False]

def test_session_extreme_tracks_hi_lo_and_pct():
    s = SessionExtreme()
    assert s.step(100.0) == (True, True)    # first tick sets both baselines
    assert s.step(101.0) == (True, False)
    assert s.step(99.0) == (False, True)
    assert abs(s.pct_chg(110.0) - 0.10) < 1e-9   # (110-100)/100
