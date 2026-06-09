from entropy.bot.signals import SignalAction
from entropy.bot.strategies.momentum_scalper import MomentumScalper
from entropy.engine.events import DownMove, NewHigh, Spike


def test_spike_emits_enter_long():
    s = MomentumScalper(min_pct=0.15)
    ev = Spike(symbol="SPY", ts_ns=1, price=101.0, pct=0.5)
    out = s.on_tick("SPY", 101.0, 1, events=[ev])
    assert len(out) == 1
    assert out[0].action is SignalAction.ENTER_LONG
    assert out[0].strategy == "momentum_scalper"
    assert out[0].strength > 0


def test_downmove_emits_enter_short():
    s = MomentumScalper(min_pct=0.15)
    ev = DownMove(symbol="SPY", ts_ns=1, price=99.0, pct=-0.30)
    out = s.on_tick("SPY", 99.0, 1, events=[ev])
    assert out[0].action is SignalAction.ENTER_SHORT


def test_below_min_pct_is_ignored():
    s = MomentumScalper(min_pct=0.40)
    ev = Spike(symbol="SPY", ts_ns=1, price=100.1, pct=0.10)
    assert s.on_tick("SPY", 100.1, 1, events=[ev]) == []


def test_non_momentum_events_ignored():
    s = MomentumScalper(min_pct=0.0)
    ev = NewHigh(symbol="SPY", ts_ns=1, price=101.0)
    assert s.on_tick("SPY", 101.0, 1, events=[ev]) == []


def test_symbol_whitelist():
    s = MomentumScalper(symbols=("BTCUSDT",), min_pct=0.0)
    ev = Spike(symbol="SPY", ts_ns=1, price=101.0, pct=1.0)
    assert s.on_tick("SPY", 101.0, 1, events=[ev]) == []


def test_warmup_is_noop():
    s = MomentumScalper()
    assert s.warmup([]) is None
