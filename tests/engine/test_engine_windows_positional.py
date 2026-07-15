from entropy.engine.engine import Engine, _WIN_ORDER
from entropy.engine.events import WindowName

_S = 1_000_000_000


def test_win_order_is_three_positional():
    assert _WIN_ORDER == (WindowName.W0, WindowName.W1, WindowName.W2)


def test_snapshot_counts_keyed_by_display_label():
    eng = Engine()
    ts = 0
    for px in (100.0, 101.0, 102.0, 101.0, 99.0):
        ts += 1 * _S
        eng.on_trade("AAA", px, 1.0, "buy", ts)
    snap = eng.snapshot()
    assert list(snap.breadth.nh_counts.keys()) == ["15m", "1h", "4h"]
    assert list(snap.breadth.nl_counts.keys()) == ["15m", "1h", "4h"]
