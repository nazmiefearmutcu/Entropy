from __future__ import annotations

import msgspec


class LeaderRow(msgspec.Struct, frozen=True):
    symbol: str
    count: int
    price: float
    pct_chg: float
