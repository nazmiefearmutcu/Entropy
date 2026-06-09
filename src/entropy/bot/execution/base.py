from __future__ import annotations

from typing import Protocol

from ..orders import Fill, Order


class ExecutionAdapter(Protocol):
    """Turns an Order into a Fill. Paper fills instantly; live routes to an exchange."""

    def submit(self, order: Order) -> Fill: ...
