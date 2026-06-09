import pytest

from entropy.bot.execution.live import LIVE_WARNING, LiveExecutor, LiveTradingDisabledError
from entropy.bot.orders import Order, OrderIntent, OrderSide


def _order() -> Order:
    return Order(id="o1", symbol="BTCUSDT", side=OrderSide.BUY, intent=OrderIntent.OPEN,
                 qty=1.0, price=50000.0, ts_ns=1, strategy="x")


def test_warning_is_english_and_mentions_real_money():
    assert "REAL money" in LIVE_WARNING
    assert "DISABLED BY DEFAULT" in LIVE_WARNING


def test_disabled_by_default_raises_with_warning():
    ex = LiveExecutor()
    with pytest.raises(LiveTradingDisabledError) as exc:
        ex.submit(_order())
    assert "DISABLED BY DEFAULT" in str(exc.value)


def test_enabled_without_risk_ack_raises():
    ex = LiveExecutor(enabled=True)
    with pytest.raises(LiveTradingDisabledError) as exc:
        ex.submit(_order())
    assert "risk" in str(exc.value).lower()


def test_enabled_and_acked_without_credentials_raises():
    ex = LiveExecutor(enabled=True, acknowledged_risk=True)
    with pytest.raises(LiveTradingDisabledError) as exc:
        ex.submit(_order())
    assert "credential" in str(exc.value).lower()


def test_fully_authorized_still_not_implemented():
    ex = LiveExecutor(enabled=True, acknowledged_risk=True,
                      api_key="k", api_secret="s")
    with pytest.raises(NotImplementedError):
        ex.submit(_order())
