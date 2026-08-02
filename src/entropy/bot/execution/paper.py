from __future__ import annotations

from ..costs import CostModel
from ..orders import Fill, Order, OrderSide


class PaperExecutor:
    """Simulates instant fills at the order's reference price ± adverse slippage, plus fees.

    ``cost_model`` resolves per-symbol fee/slippage (market-aware by default);
    without one the flat ``fee_bps``/``slippage_bps`` apply to every symbol.
    """

    def __init__(
        self,
        fee_bps: float = 1.0,
        slippage_bps: float = 1.0,
        cost_model: CostModel | None = None,
    ) -> None:
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.cost_model = cost_model

    def submit(self, order: Order) -> Fill:
        costs = (
            self.cost_model.for_symbol(order.symbol)
            if self.cost_model is not None
            else None
        )
        fee_bps = costs.fee_bps if costs is not None else self.fee_bps
        slippage_bps = costs.slippage_bps if costs is not None else self.slippage_bps
        slip = order.price * (slippage_bps / 10_000.0)
        fill_px = order.price + slip if order.side is OrderSide.BUY else order.price - slip
        fee = abs(fill_px * order.qty) * (fee_bps / 10_000.0)
        return Fill(
            order_id=order.id, symbol=order.symbol, side=order.side, qty=order.qty,
            price=fill_px, fee=fee, slippage=slip, ts_ns=order.ts_ns,
        )
