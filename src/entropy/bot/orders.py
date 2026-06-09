from __future__ import annotations

import enum

import msgspec


class OrderSide(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderIntent(enum.StrEnum):
    OPEN = "open"
    CLOSE = "close"
    STOP = "stop"
    TAKE_PROFIT = "take_profit"


class Order(msgspec.Struct, frozen=True):
    id: str
    symbol: str
    side: OrderSide
    intent: OrderIntent
    qty: float
    price: float  # mark price at decision time (paper-fill reference)
    ts_ns: int
    strategy: str


class Fill(msgspec.Struct, frozen=True):
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float  # executed price incl. slippage
    fee: float
    slippage: float
    ts_ns: int
