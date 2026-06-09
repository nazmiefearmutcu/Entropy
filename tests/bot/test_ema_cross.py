from entropy.bot.signals import SignalAction
from entropy.bot.strategies.ema_cross import EmaCrossStrategy


def test_warmup_then_crossover_emits_enter_long():
    strat = EmaCrossStrategy(symbol="SPY", fast=2, slow=4)
    # warm up flat at 100, then ramp up to force fast>slow crossover
    from entropy.strategy.engine import Bar
    strat.warmup([Bar(ts_ns=i, close=100.0) for i in range(4)])
    signals = []
    for i, px in enumerate([101, 102, 103, 104, 105], start=10):
        signals += strat.on_tick("SPY", float(px), i, events=[])
    assert any(s.action is SignalAction.ENTER_LONG for s in signals)
    assert all(s.strategy == "ema_cross" for s in signals)


def test_ignores_other_symbols():
    strat = EmaCrossStrategy(symbol="SPY", fast=2, slow=4)
    from entropy.strategy.engine import Bar
    strat.warmup([Bar(ts_ns=i, close=100.0) for i in range(4)])
    assert strat.on_tick("BTCUSDT", 999.0, 1, events=[]) == []


def test_name_attribute():
    assert EmaCrossStrategy(symbol="SPY").name == "ema_cross"
