# tests/engine/test_breadth.py
from entropy.engine.breadth import BreadthTracker

S = 1_000_000_000


def test_sell_buy_pct_amount_weighted():
    b = BreadthTracker(window_s=30)
    b.add_trade("buy", 30.0, 0)
    b.add_trade("sell", 70.0, 0)
    assert abs(b.sell_pct() - 70.0) < 1e-9
    assert abs(b.buy_pct() - 30.0) < 1e-9


def test_raw_hz_and_event_rate():
    b = BreadthTracker(window_s=30)
    for _ in range(4000):
        b.tick(10 * S)
    b.events(10 * S, 3)
    assert b.raw_hz() == 4000.0


def test_accel_flag():
    b = BreadthTracker(window_s=30)
    assert b.accel(prev_rate=0.0) == "steady"
    b._event_meter.add(0, 100)
    assert b.accel(prev_rate=1.0) == "accelerating"
