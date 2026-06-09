from entropy.bot.execution.paper import PaperExecutor
from entropy.bot.orders import Order, OrderIntent, OrderSide


def _order(side: OrderSide) -> Order:
    return Order(id="o1", symbol="SPY", side=side, intent=OrderIntent.OPEN,
                 qty=10.0, price=100.0, ts_ns=1, strategy="x")


def test_buy_fills_above_with_slippage_and_fee():
    ex = PaperExecutor(fee_bps=10.0, slippage_bps=5.0)  # 0.10% fee, 0.05% slippage
    f = ex.submit(_order(OrderSide.BUY))
    assert f.slippage == 100.0 * 0.0005
    assert f.price == 100.0 + f.slippage  # buy fills higher (adverse)
    assert f.fee == abs(f.price * 10.0) * 0.001


def test_sell_fills_below_with_slippage():
    ex = PaperExecutor(fee_bps=0.0, slippage_bps=5.0)
    f = ex.submit(_order(OrderSide.SELL))
    assert f.price == 100.0 - 100.0 * 0.0005  # sell fills lower (adverse)
    assert f.fee == 0.0


def test_fill_carries_order_identity():
    ex = PaperExecutor()
    f = ex.submit(_order(OrderSide.BUY))
    assert f.order_id == "o1"
    assert f.symbol == "SPY"
    assert f.qty == 10.0
