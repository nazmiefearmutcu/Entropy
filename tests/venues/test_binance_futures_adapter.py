"""BinanceFuturesAdapter: pure delegation, zero logic moves.

Proves the wrapper adds nothing but venue_id/is_market_open and that the
venues package never imports scripts/ (the supervisor injects the instance).
"""
from __future__ import annotations

from pathlib import Path

import entropy.venues.binance_futures as bf
from entropy.venues import BinanceFuturesAdapter

from .conftest import StubExecutor


def test_requires_injected_executor():
    try:
        BinanceFuturesAdapter(None)
    except ValueError as exc:
        assert "injected executor" in str(exc)
    else:
        raise AssertionError("None executor must be rejected")


def test_attribute_delegation():
    ex = StubExecutor()
    a = BinanceFuturesAdapter(ex)
    assert a.venue_id == "binance-futures"
    assert a.host == ex.host
    assert a.sizing_pct == 0.30
    assert a.leverage == 10.0
    assert a.slots == 3


def test_method_delegation_records_and_returns_verbatim():
    ex = StubExecutor()
    a = BinanceFuturesAdapter(ex)
    assert a.place_market_order("ETHUSDT", "SELL", 1.5, reduce_only=True) == \
        {"ok": True, "order_id": "oid1", "status": "FILLED",
         "avg_price": 100.0, "qty_used": 1.5, "bumped": False}
    assert ex.calls[-1] == ("place_market_order", "ETHUSDT", "SELL", 1.5, True)
    assert a.place_venue_stop("ETHUSDT", "long", 99.5) == "stop1"
    assert a.place_venue_tp("ETHUSDT", "long", 1.0, 110.0) == "tp1"
    assert a.cancel_venue_stop("ETHUSDT") is True
    assert a.cancel_stop_by_id("ETHUSDT", "9") is True
    assert a.cancel_stale_venue_stops(["ETHUSDT"]) == 1
    assert a.cancel_venue_tp("ETHUSDT") is True
    assert a.adopt_open_stop("ETHUSDT", "short") == "adopt1"
    assert a.adopt_open_tp("ETHUSDT", "short") == "tp1"
    assert a.algo_order_alive("ETHUSDT", "1") is True
    assert a.order_alive("ETHUSDT", "1") is True
    assert a.sweep_stale_stops("ETHUSDT", "long") == 2
    assert a.get_balance()["wallet"] == 1000.0
    assert a.get_open_positions()[0]["symbol"] == "ETHUSDT"
    assert a.get_price("ETHUSDT") == 100.0
    # names land on the executor EXACTLY as the runner feature-detects them
    assert [c[0] for c in ex.calls] == [
        "place_market_order", "place_venue_stop", "place_venue_tp",
        "cancel_venue_stop", "cancel_stop_by_id", "cancel_stale_venue_stops",
        "cancel_venue_tp", "adopt_open_stop", "adopt_open_tp",
        "algo_order_alive", "order_alive", "sweep_stale_stops",
        "get_balance", "get_open_positions", "get_price"]


def test_ban_delegation():
    ex = StubExecutor()
    a = BinanceFuturesAdapter(ex)
    assert a.banned_until_ms() == 0
    assert a.is_banned(0.0) is False
    ex.banned_ms = 5_000
    assert a.banned_until_ms() == 5_000
    assert a.is_banned(1_000.0) is True


def test_market_open_is_always_true_and_funding_passes_through():
    ex = StubExecutor()
    a = BinanceFuturesAdapter(ex)
    assert a.is_market_open(0) is True
    assert a.is_market_open(1_700_000_000_000) is True
    assert a.session_label(0) == "24/7"
    assert a.get_funding_rate("ETHUSDT") == {
        "last_funding_rate": 0.0001, "mark_price": 100.0,
        "next_funding_ms": 0}
    assert ex.calls[-1] == ("get_funding_rate", "ETHUSDT")


def test_no_scripts_import_in_the_venues_package():
    """The adapter must wrap an INJECTED instance; importing scripts/kaos_
    testnet_exec.py from the package is forbidden (not a package module)."""
    src = Path(bf.__file__).read_text(encoding="utf-8")
    assert "import kaos_testnet_exec" not in src
    assert "from kaos_testnet_exec" not in src
    assert "from scripts" not in src
    assert "importlib" not in src
