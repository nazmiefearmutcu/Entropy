"""Shared zero-network harness for the venues test package.

- ``StubExecutor``: minimal stand-in for the real crypto executor
  (TestnetExecutor-shaped) — records every call, returns canned values. It
  drives BinanceFuturesAdapter the same way the Wave-2 supervisor will drive
  the production executor.
- ``FakeTransport``: replaces urllib inside AlpacaEquitiesAdapter (constructor
  injection) — records (method, url, headers, body) and answers from a route
  table. NO test ever touches the network.
"""
from __future__ import annotations

import json
from collections import deque
from typing import Any

import pytest

from entropy.venues import AlpacaEquitiesAdapter, BinanceFuturesAdapter

PAPER = "https://paper-api.alpaca.markets"


# ---- canonical surface (method names are load-bearing — verbatim) -----------

PROTOCOL_METHODS = (
    "place_market_order", "place_venue_stop", "place_venue_tp",
    "cancel_venue_stop", "cancel_stop_by_id", "cancel_stale_venue_stops",
    "cancel_venue_tp", "adopt_open_stop", "adopt_open_tp",
    "algo_order_alive", "order_alive", "sweep_stale_stops",
    "get_balance", "get_open_positions", "get_price", "get_funding_rate",
    "banned_until_ms", "is_banned", "is_market_open",
)


# ---- stub crypto executor ----------------------------------------------------

class StubExecutor:
    """TestnetExecutor stand-in: canned answers + a call recorder."""

    def __init__(self) -> None:
        self.host = "https://testnet.binancefuture.com"
        self.sizing_pct = 0.30
        self.leverage = 10
        self.slots = 3
        self.last_errors: deque = deque(maxlen=5)
        self.calls: list[tuple] = []
        self.banned_ms = 0

    def place_market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> dict:
        self.calls.append(("place_market_order", symbol, side, qty,
                           reduce_only))
        return {"ok": True, "order_id": "oid1", "status": "FILLED",
                "avg_price": 100.0, "qty_used": float(qty), "bumped": False}

    def place_venue_stop(self, sym: str, pos_side: str,
                         stop_price: float) -> str | None:
        self.calls.append(("place_venue_stop", sym, pos_side, stop_price))
        return "stop1"

    def place_venue_tp(self, sym: str, pos_side: str, qty: float,
                       tp_price: float) -> str | None:
        self.calls.append(("place_venue_tp", sym, pos_side, qty, tp_price))
        return "tp1"

    def cancel_venue_stop(self, sym: str) -> bool:
        self.calls.append(("cancel_venue_stop", sym))
        return True

    def cancel_stop_by_id(self, sym: str, order_id: str) -> bool:
        self.calls.append(("cancel_stop_by_id", sym, order_id))
        return True

    def cancel_stale_venue_stops(self, symbols: Any = None) -> int:
        self.calls.append(("cancel_stale_venue_stops", symbols))
        return 1

    def cancel_venue_tp(self, sym: str) -> bool:
        self.calls.append(("cancel_venue_tp", sym))
        return True

    def adopt_open_stop(self, sym: str, pos_side: str = "long") -> str | None:
        self.calls.append(("adopt_open_stop", sym, pos_side))
        return "adopt1"

    def adopt_open_tp(self, sym: str, pos_side: str) -> str | None:
        self.calls.append(("adopt_open_tp", sym, pos_side))
        return "tp1"

    def algo_order_alive(self, sym: str, algoid: str) -> bool:
        self.calls.append(("algo_order_alive", sym, algoid))
        return True

    def order_alive(self, sym: str, order_id: str) -> bool:
        self.calls.append(("order_alive", sym, order_id))
        return True

    def sweep_stale_stops(self, sym: str, pos_side: str) -> int:
        self.calls.append(("sweep_stale_stops", sym, pos_side))
        return 2

    def get_balance(self) -> dict:
        self.calls.append(("get_balance",))
        return {"wallet": 1000.0, "available": 900.0, "unrealized": 0.5}

    def get_open_positions(self) -> list[dict]:
        self.calls.append(("get_open_positions",))
        return [{"symbol": "ETHUSDT", "contracts": 2.0, "side": "long",
                 "qty": 2.0, "entry_price": 100.0, "mark_price": 101.0,
                 "unrealized": 2.0, "notional": 202.0, "leverage": 10.0,
                 "isolated": True}]

    def get_price(self, symbol: str) -> float:
        self.calls.append(("get_price", symbol))
        return 100.0

    def get_funding_rate(self, symbol: str) -> dict:
        self.calls.append(("get_funding_rate", symbol))
        return {"last_funding_rate": 0.0001, "mark_price": 100.0,
                "next_funding_ms": 0}

    def banned_until_ms(self) -> int:
        return self.banned_ms

    def is_banned(self, now_ms: float | None = None) -> bool:
        return self.banned_ms > (now_ms or 0.0)


# ---- fake HTTP transport -------------------------------------------------------

class FakeTransport:
    """Route-table transport: request() -> first matching route."""

    def __init__(self, routes: list[tuple[str, str, int, Any]] | None = None,
                 responder: Any | None = None) -> None:
        self.calls: list[dict] = []
        self.routes = list(routes or [])
        self._responder = responder

    def request(self, method: str, url: str, headers: dict | None = None,
                body: bytes | None = None) -> tuple[int, Any]:
        record = {"method": method, "url": url, "headers": dict(headers or {}),
                  "body": body}
        self.calls.append(record)
        if self._responder is not None:
            return self._responder(method, url, headers, body)
        for m, path_sub, status, payload in self.routes:
            if m == method and path_sub in url:
                return status, payload
        return 404, {"message": f"no route for {method} {url}"}

    def bodies(self) -> list[Any]:
        out = []
        for c in self.calls:
            out.append(json.loads(c["body"].decode("utf-8"))
                       if c["body"] else None)
        return out


# ---- builders -------------------------------------------------------------------

@pytest.fixture
def stub_executor() -> StubExecutor:
    return StubExecutor()


@pytest.fixture
def binance_adapter(stub_executor) -> BinanceFuturesAdapter:
    return BinanceFuturesAdapter(stub_executor)


def make_alpaca(routes: list[tuple[str, str, int, Any]] | None = None,
                **kwargs: Any) -> tuple[AlpacaEquitiesAdapter, FakeTransport]:
    transport = FakeTransport(routes)
    kwargs.setdefault("api_key_id", "key123")
    kwargs.setdefault("api_secret", "sec123")
    adapter = AlpacaEquitiesAdapter(transport=transport, **kwargs)
    return adapter, transport


# canned venue payloads shared by several suites
ACCOUNT_RICH = {"equity": "50000", "cash": "40000",
                "unrealized_pl": "12.5", "last_equity": "49900"}
ACCOUNT_POOR = {"equity": "20000", "cash": "15000",
                "unrealized_pl": "-3.0", "last_equity": "20050"}
POS_AAPL_LONG = {"symbol": "AAPL", "qty": "5",
                 "avg_entry_price": "100.10", "current_price": "101.00",
                 "unrealized_pl": "4.50", "market_value": "505.00"}
POS_AAPL_SHORT = {"symbol": "AAPL", "qty": "-5",
                  "avg_entry_price": "100.10", "current_price": "99.00",
                  "unrealized_pl": "5.50", "market_value": "-495.00"}
LATEST_TRADE = {"symbol": "AAPL", "trade": {"p": 101.25, "s": 10,
                                            "t": "2026-09-09T14:30:00Z"}}
OPEN_STOP_ROW = {"id": "stop-9", "symbol": "AAPL", "side": "sell",
                 "type": "stop", "status": "open",
                 "client_order_id": "KAOS-S1", "qty": "5"}
OPEN_TP_ROW = {"id": "tp-7", "symbol": "AAPL", "side": "sell",
               "type": "limit", "status": "open",
               "client_order_id": "KAOS-T1", "qty": "5"}
OPEN_OTHER_ROW = {"id": "buy-1", "symbol": "AAPL", "side": "buy",
                  "type": "limit", "status": "open",
                  "client_order_id": "manual-1", "qty": "5"}
ORDER_PLACED = {"id": "oid-new", "symbol": "AAPL", "qty": "10",
                "side": "buy", "type": "market", "status": "new",
                "filled_avg_price": None}


def alpaca_routes(**overrides: Any) -> list[tuple[str, str, int, Any]]:
    """Standard route table: rich account, long position, live order."""
    return [
        ("GET", "/v2/account", 200, overrides.get("account", ACCOUNT_RICH)),
        ("GET", "/v2/positions", 200,
         overrides.get("positions", [POS_AAPL_LONG])),
        ("GET", "trades:latest", 200, overrides.get("price", LATEST_TRADE)),
        ("GET", "/v2/orders", 200,
         overrides.get("orders", [OPEN_STOP_ROW, OPEN_TP_ROW,
                                  OPEN_OTHER_ROW])),
        ("POST", "/v2/orders", 200, overrides.get("order", ORDER_PLACED)),
        ("DELETE", "/v2/orders/", 204, overrides.get("delete", {})),
        ("GET", "/v2/stocks/AAPL/bars", 200, overrides.get("bars", {"bars": []})),
    ]
