from entropy.strategy.engine import StrategyEvent, EventKind
from entropy.strategy.format import render_event

def test_open_long_format():
    e = StrategyEvent(EventKind.OPEN_LONG, 1, "SPY", 749.886, running_pnl=0.0)
    text, color = render_event(e)
    assert text == "OPEN LONG @ 749.886 running_pnl=0.000" and color == "green"

def test_close_short_format():
    e = StrategyEvent(EventKind.CLOSE_SHORT, 1, "SPY", 748.435, trade_pnl=-0.135)
    text, color = render_event(e)
    assert text == "CLOSE SHORT @ 748.435 trade_pnl=-0.135" and color == "yellow"

def test_info_default():
    e = StrategyEvent(EventKind.INFO, 1, "SPY", 0.0, text="watching [SPY]")
    assert render_event(e) == ("watching [SPY]", "white")
