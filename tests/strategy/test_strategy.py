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
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3))
    # directly exercise pnl helper via a short then close
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
