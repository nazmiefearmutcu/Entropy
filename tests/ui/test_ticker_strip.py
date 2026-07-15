from entropy.engine.engine import TickerGroup
from entropy.ui.widgets.highlow_gauges import bar
from entropy.ui.widgets.ticker_strip import format_groups


def test_format_groups_renders_window_and_symbols():
    groups = (
        TickerGroup(window="15m", entries=(("GWW", 15), ("APP", 13))),
        TickerGroup(window="1h", entries=(("ASML", 18),)),
    )
    text = format_groups(groups).plain
    assert "15m:" in text and "GWW 15" in text and "APP 13" in text
    assert "1h:" in text and "ASML 18" in text


def test_format_groups_empty():
    assert format_groups(()).plain == ""


def test_bar_proportional():
    assert bar(0, 10, 8) == ""
    assert bar(10, 10, 8) == "█" * 8
    assert bar(5, 10, 8) == "█" * 4
    assert bar(1, 1000, 8) == "█"          # tiny but nonzero -> at least one block
