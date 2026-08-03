import pytest

from entropy.bot.costs import CostModel, MarketClass, MarketCosts
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


def test_cost_model_resolves_per_market_fee_and_slippage():
    cm = CostModel(flat_fee_bps=1.0, flat_slippage_bps=1.0,
                   market={MarketClass.CRYPTO_SPOT: MarketCosts(10.0, 3.0)})
    ex = PaperExecutor(fee_bps=1.0, slippage_bps=1.0, cost_model=cm)
    spot = Order(id="o1", symbol="SOLUSDT", side=OrderSide.BUY, intent=OrderIntent.OPEN,
                 qty=10.0, price=100.0, ts_ns=1, strategy="x")
    equity = Order(id="o2", symbol="SPY", side=OrderSide.BUY, intent=OrderIntent.OPEN,
                   qty=10.0, price=100.0, ts_ns=1, strategy="x")
    f_spot = ex.submit(spot)
    f_equity = ex.submit(equity)
    # Spot: 10 bps fee + 3 bps adverse slip; equity: flat 1/1.
    assert f_spot.slippage == pytest.approx(100.0 * 3.0 / 10_000.0)
    assert f_spot.fee == pytest.approx(abs(f_spot.price * 10.0) * 10.0 / 10_000.0)
    assert f_equity.slippage == pytest.approx(100.0 * 1.0 / 10_000.0)
    assert f_equity.fee == pytest.approx(abs(f_equity.price * 10.0) * 1.0 / 10_000.0)


def test_cost_model_partial_override_inherits_flat_fallback():
    cm = CostModel(flat_fee_bps=1.0, flat_slippage_bps=2.0,
                   market={MarketClass.CRYPTO_SPOT: MarketCosts(10.0, None)})
    ex = PaperExecutor(fee_bps=1.0, slippage_bps=2.0, cost_model=cm)
    order = Order(id="o1", symbol="SOLUSDT", side=OrderSide.SELL,
                  intent=OrderIntent.OPEN, qty=10.0, price=100.0, ts_ns=1, strategy="x")
    f = ex.submit(order)
    # The unset slippage inherits the flat 2.0 bps; the fee override stays 10 bps.
    assert f.slippage == pytest.approx(100.0 * 2.0 / 10_000.0)
    assert f.fee == pytest.approx(abs(f.price * 10.0) * 10.0 / 10_000.0)
