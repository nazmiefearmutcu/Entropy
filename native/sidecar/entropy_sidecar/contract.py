from __future__ import annotations

import msgspec

Leader = tuple[str, int, float, float]      # (symbol, count, price, pct)
TickerEntry = tuple[str, list[tuple[str, int]]]  # (window, [(symbol, count)])
Candle = tuple[int, float, float, float, float, float]  # (ts_ns, o, h, l, c, v)
Level = tuple[float, float]                 # (price, size)


class DepthLevels(msgspec.Struct, frozen=True):
    basis: str
    is_synthetic: bool
    reference_price: float
    bids: list[Level]
    asks: list[Level]


class Fundamentals(msgspec.Struct, frozen=True):
    pe: float | None = None
    market_cap: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None


class FocusView(msgspec.Struct, frozen=True):
    symbol: str
    asset: str                              # "EQUITY" | "CRYPTO" | "SIM"
    last: float | None
    pct: float | None
    hi: float | None
    lo: float | None
    candles: list[Candle]
    depth: DepthLevels | None
    fundamentals: Fundamentals | None


class SnapshotMessage(msgspec.Struct, frozen=True, tag="snapshot", tag_field="type"):
    schema_version: int
    ts_ns: int
    buy_pct: float
    sell_pct: float
    raw_hz: float
    accel: str
    new_highs: list[Leader]
    new_lows: list[Leader]
    ticker: list[TickerEntry]
    focus: FocusView
    watchlist: list[tuple[str, float | None, float | None, list[float]]]
    market_status: str
    source: str


class CommandRequest(msgspec.Struct, frozen=True):
    verb: str
    arg: str = ""


class CommandResult(msgspec.Struct, frozen=True):
    ok: bool
    message: str
