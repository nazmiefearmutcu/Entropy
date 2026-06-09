from entropy.bot.orders import Fill, Order, OrderIntent, OrderSide
from entropy.bot.signals import Signal, SignalAction


def test_signal_is_frozen_with_fields():
    s = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=0.8,
               reason="test", ts_ns=1, strategy="x")
    assert s.action is SignalAction.ENTER_LONG
    assert s.strength == 0.8


def test_order_and_fill_construct():
    o = Order(id="o1", symbol="SPY", side=OrderSide.BUY, intent=OrderIntent.OPEN,
              qty=10.0, price=100.0, ts_ns=1, strategy="x")
    f = Fill(order_id=o.id, symbol="SPY", side=OrderSide.BUY, qty=10.0,
             price=100.1, fee=0.1, slippage=0.1, ts_ns=2)
    assert o.intent is OrderIntent.OPEN
    assert f.price == 100.1
