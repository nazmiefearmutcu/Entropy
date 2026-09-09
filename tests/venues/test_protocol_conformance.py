"""Protocol conformance: the SAME surface semantics on BOTH adapters.

The shared harness drives BinanceFuturesAdapter around a stub executor and
AlpacaEquitiesAdapter around a fake HTTP transport, then asserts the result
envelopes have identical kinds/keys for the whole load-bearing surface
(method names verbatim from scripts/kaos_testnet_exec.py — see base.py).
Zero network: crypto side never leaves the stub; Alpaca side never leaves the
FakeTransport.
"""
from __future__ import annotations

from entropy.venues import BinanceFuturesAdapter, VenueAdapter
from entropy.venues.clock import CryptoClock

from .conftest import PROTOCOL_METHODS, alpaca_routes, make_alpaca


class _OpenClock:
    """Injectable clock so the equities venue tests don't need a wall time."""

    def is_open(self, now_ms: int) -> bool:
        return True

    def session_label(self, now_ms: int) -> str:
        return "RTH"


def _both(**kwargs):
    """(binance adapter over stub, alpaca adapter over fake transport)."""
    from .conftest import StubExecutor

    ex = StubExecutor()
    b = BinanceFuturesAdapter(ex)
    # specific single-order GET route FIRST (first-match-wins substring table)
    routes = ([("GET", "/v2/orders/1", 200,
                {"id": "1", "status": "new"})]
              + alpaca_routes())
    a, transport = make_alpaca(routes, clock=_OpenClock(), **kwargs)
    return b, a, ex, transport


def test_both_adapters_satisfy_the_protocol():
    for adapter in (_both()[0], _both()[1]):
        assert isinstance(adapter, VenueAdapter)


def test_both_adapters_expose_every_load_bearing_method():
    _, a, _, _ = _both()
    for name in PROTOCOL_METHODS:
        assert callable(getattr(a, name)), name
        assert callable(getattr(BinanceFuturesAdapter, name)), name


def test_place_market_order_same_envelope():
    b, a, ex, transport = _both()
    for adapter in (b, a):
        res = adapter.place_market_order("SYM", "buy", 10.0)
        assert res["ok"] is True
        assert isinstance(res["order_id"], str) and res["order_id"]
        assert isinstance(res["status"], str)
        assert isinstance(res["qty_used"], float)
    assert ex.calls[0][:3] == ("place_market_order", "SYM", "buy")
    body = transport.bodies()[-1]
    assert body["type"] == "market" and body["qty"] == "10"


def test_place_market_order_reduce_only_same_envelope():
    b, a, ex, transport = _both()
    rb = b.place_market_order("SYM", "sell", 2.0, reduce_only=True)
    ra = a.place_market_order("AAPL", "sell", 2.0, reduce_only=True)
    for res in (rb, ra):
        assert res["ok"] is True and res.get("skipped") is None
        assert isinstance(res["order_id"], str) and res["order_id"]
    assert ex.calls[0][4] is True
    body = transport.bodies()[-1]
    # close qty clamped to min(requested 2, position 5)
    assert body["side"] == "sell" and body["qty"] == "2"


def test_place_venue_stop_returns_id_on_both():
    b, a, ex, transport = _both()
    assert isinstance(b.place_venue_stop("SYM", "long", 99.0), str)
    assert isinstance(a.place_venue_stop("AAPL", "long", 99.0), str)


def test_place_venue_tp_returns_id_on_both():
    b, a, ex, transport = _both()
    assert isinstance(b.place_venue_tp("SYM", "long", 1.0, 110.0), str)
    assert isinstance(a.place_venue_tp("AAPL", "long", 5.0, 110.0), str)


def test_bracket_bookkeeping_same_semantics():
    b, a, ex, transport = _both()
    # cancel paths cancel the adapter's TRACKED order ids (crypto parity):
    # prime the equities map the way place_venue_stop/adopt would have.
    a._stop_orders["AAPL"] = "stop-9"
    assert b.cancel_venue_stop("SYM") is True
    assert a.cancel_venue_stop("AAPL") is True
    assert b.cancel_stop_by_id("SYM", "7") is True
    assert a.cancel_stop_by_id("AAPL", "7") is True
    a._stop_orders["AAPL"] = "stop-9"
    assert b.cancel_stale_venue_stops(["SYM"]) == 1
    assert a.cancel_stale_venue_stops(["AAPL"]) == 1
    a._tp_orders["AAPL"] = "tp-7"
    assert b.cancel_venue_tp("SYM") is True
    assert a.cancel_venue_tp("AAPL") is True
    assert isinstance(b.adopt_open_stop("SYM", "long"), str)
    assert isinstance(a.adopt_open_stop("AAPL", "long"), str)
    assert isinstance(b.adopt_open_tp("SYM", "long"), str)
    assert isinstance(a.adopt_open_tp("AAPL", "long"), str)
    assert isinstance(b.sweep_stale_stops("SYM", "long"), int)
    assert isinstance(a.sweep_stale_stops("AAPL", "long"), int)
    assert b.algo_order_alive("SYM", "1") is True
    assert a.algo_order_alive("AAPL", "1") is True
    assert b.order_alive("SYM", "1") is True
    assert a.order_alive("AAPL", "1") is True


def test_reads_same_schema():
    b, a, ex, transport = _both()
    for adapter in (b, a):
        bal = adapter.get_balance()
        assert {"wallet", "available", "unrealized"} <= set(bal)
        assert isinstance(bal["wallet"], float)
        positions = adapter.get_open_positions()
        assert isinstance(positions, list) and positions
        assert {"symbol", "contracts", "side", "qty"} <= set(positions[0])
        assert positions[0]["side"] in ("long", "short")
        assert isinstance(adapter.get_price("X"), float)
        assert isinstance(adapter.banned_until_ms(), int)
        assert adapter.banned_until_ms() >= 0
        assert adapter.is_banned(0.0) is False
        assert adapter.is_market_open(0) is True  # open clock injected


def test_funding_contract_split():
    """Crypto passthrough returns a REAL rate dict; equities ALWAYS None
    (M2 funding gate no-ops on NASDAQ) — both valid per the protocol."""
    b, a, ex, transport = _both()
    assert isinstance(b.get_funding_rate("SYM"), dict)
    assert a.get_funding_rate("AAPL") is None


def test_crypto_clock_is_24_7():
    assert isinstance(b := _both()[0].clock, CryptoClock)
    assert b.is_open(0) is True
    assert b.session_label(0) == "24/7"
