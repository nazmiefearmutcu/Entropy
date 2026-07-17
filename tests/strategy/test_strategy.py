from entropy.strategy.engine import Bar, EventKind, Side, Strategy, StrategyConfig


def _warm(s, closes, t0=0, dt=1):
    return s.warmup([Bar(ts_ns=(t0 + i) * dt, close=c) for i, c in enumerate(closes)])

def test_warmup_info_and_warm_flag():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3))
    evs = _warm(s, [100, 100, 100])
    assert evs and evs[0].kind == EventKind.INFO
    assert s.is_warm and s.position.side == Side.FLAT

def test_golden_long_then_flip_to_short():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3, size=1.0, fee_bps=0.0))
    _warm(s, [100, 100, 100])
    assert s.on_price("T", 100.0, 10) == []                       # signal 0
    o = s.on_price("T", 101.0, 11)
    assert len(o) == 1 and o[0].kind == EventKind.OPEN_LONG
    s.on_price("T", 103.0, 12)
    flip = s.on_price("T", 99.0, 13)
    kinds = [e.kind for e in flip]
    assert kinds == [EventKind.CLOSE_LONG, EventKind.OPEN_SHORT]
    assert abs(flip[0].trade_pnl - (-2.0)) < 1e-9                 # 99-101

def test_short_sign_matches_gif():
    # directly exercise the pnl helper for the SHORT/LONG sign convention
    from entropy.strategy.engine import _gross_pnl
    assert abs(_gross_pnl(Side.SHORT, 748.300, 748.435, 1.0) - (-0.135)) < 1e-9
    assert abs(_gross_pnl(Side.LONG, 749.886, 750.025, 1.0) - 0.139) < 1e-9

def test_fee_applied_on_close():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3, fee_bps=10.0))
    _warm(s, [100, 100, 100])
    s.on_price("T", 101.0, 11)         # OPEN_LONG @101
    flip = s.on_price("T", 99.0, 13)   # CLOSE_LONG @99
    # gross -2.0; fees = 101*0.001 + 99*0.001 = 0.2 -> -2.2
    assert abs(flip[0].trade_pnl - (-2.2)) < 1e-9

def test_long_only_guard():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3, allow_short=False))
    _warm(s, [100, 100, 100])
    s.on_price("T", 101.0, 11)
    out = s.on_price("T", 99.0, 13)
    assert [e.kind for e in out] == [EventKind.CLOSE_LONG]
    assert s.position.side == Side.FLAT

def test_symbol_mismatch_ignored():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3))
    _warm(s, [100, 100, 100])
    assert s.on_price("OTHER", 101.0, 11) == []

def test_running_pnl_mark():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3, size=2.0))
    _warm(s, [100, 100, 100])
    s.on_price("T", 101.0, 11)         # OPEN_LONG size 2
    assert abs(s.running_pnl(104.0) - 6.0) < 1e-9

def test_tick_driven_warm_transition_emits_no_phantom_entry():
    """Without warmup() (the bot-runner path) the tick that makes the strategy
    warm must only ESTABLISH the baseline sign: a rising tape used to fire
    OPEN_LONG off the stale _prev_sign=0 with no actual crossover."""
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3))
    evs = []
    for i, px in enumerate((100.0, 101.0, 102.0)):  # warm on the 3rd tick, fast>slow
        evs += s.on_price("T", px, i)
    assert evs == []                       # no phantom entry at the transition
    assert s.position.side == Side.FLAT
    assert s._prev_sign == 1               # baseline established from the tape
    # A genuine crossover after the transition still fires normally.
    out = []
    for i, px in enumerate((95.0, 90.0), start=10):
        out += s.on_price("T", px, i)
    assert EventKind.OPEN_SHORT in [e.kind for e in out]

def test_warmup_path_unchanged_first_cross_still_fires():
    """The UI path calls warmup(bars): the first on_price after a flat warmup
    (prev sign 0, strategy already warm) must keep emitting on a real cross."""
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3))
    _warm(s, [100, 100, 100])              # warm, _prev_sign == 0 (flat)
    assert s.on_price("T", 100.0, 10) == []
    o = s.on_price("T", 101.0, 11)
    assert [e.kind for e in o] == [EventKind.OPEN_LONG]
