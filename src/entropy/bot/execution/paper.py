from __future__ import annotations

from ..orders import Fill, Order, OrderSide


class PaperExecutor:
    """Simulates instant fills at the order's reference price ± adverse slippage, plus fees."""

    def __init__(self, fee_bps: float = 1.0, slippage_bps: float = 1.0) -> None:
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def submit(self, order: Order) -> Fill:
        slip = order.price * (self.slippage_bps / 10_000.0)
        fill_px = order.price + slip if order.side is OrderSide.BUY else order.price - slip
        fee = abs(fill_px * order.qty) * (self.fee_bps / 10_000.0)
        return Fill(
            order_id=order.id, symbol=order.symbol, side=order.side, qty=order.qty,
            price=fill_px, fee=fee, slippage=slip, ts_ns=order.ts_ns,
        )
