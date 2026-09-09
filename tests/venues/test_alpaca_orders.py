"""AlpacaEquitiesAdapter order semantics: JSON bodies, rounding, close-only,
PDT gate, honest inertness, connect-test/live gating, bracket bookkeeping.

Zero network: every request lands on the FakeTransport route table.
"""
from __future__ import annotations

import pytest

from entropy.venues import AlpacaEquitiesAdapter, VenueError, VenueUnavailable
from entropy.venues.alpaca_equities import LIVE_HOST, PAPER_HOST, PdtCounter

from .conftest import (
    ACCOUNT_POOR,
    ACCOUNT_RICH,
    OPEN_STOP_ROW,
    POS_AAPL_LONG,
    FakeTransport,
    alpaca_routes,
    make_alpaca,
)

PRICE_100 = {"trade": {"p": 100.0}}


def _orders(transport: FakeTransport) -> list[dict]:
    return [b for b in transport.bodies()
            if isinstance(b, dict) and "type" in b and "symbol" in b]


def _post_urls(transport: FakeTransport) -> list[str]:
    return [c["url"] for c in transport.calls if c["method"] == "POST"]


# ---- order JSON bodies --------------------------------------------------------

def test_market_order_body_paper_host_tif_client_id():
    a, t = make_alpaca(alpaca_routes())
    res = a.place_market_order("nasdaq:AAPL", "buy", 10.0)
    assert res["ok"] is True and res["order_id"] == "oid-new"
    assert _post_urls(t) == [f"{PAPER_HOST}/v2/orders"]
    body = _orders(t)[0]
    assert body == {
        "symbol": "AAPL", "qty": "10", "side": "buy", "type": "market",
        "time_in_force": "day", "extended_hours": False,
        "client_order_id": body["client_order_id"],
    }
    assert body["client_order_id"].startswith("KAOS-")


def test_limit_stop_stop_limit_bodies():
    a, t = make_alpaca(alpaca_routes())
    a.place_order("AAPL", "sell", 5, order_type="limit",
                  time_in_force="gtc", limit_price=110.0)
    a.place_order("AAPL", "sell", 5, order_type="stop",
                  time_in_force="gtc", stop_price=99.0)
    a.place_order("AAPL", "buy", 5, order_type="stop_limit",
                  time_in_force="day", limit_price=98.5, stop_price=99.25)
    bodies = _orders(t)
    assert bodies[0]["type"] == "limit" and bodies[0]["limit_price"] == "110.00"
    assert bodies[0]["time_in_force"] == "gtc"
    assert bodies[1]["type"] == "stop" and bodies[1]["stop_price"] == "99.00"
    assert "limit_price" not in bodies[1]
    assert bodies[2]["type"] == "stop_limit"
    assert bodies[2]["limit_price"] == "98.50"
    assert bodies[2]["stop_price"] == "99.25"
    assert all(b["extended_hours"] is False for b in bodies)


def test_unsupported_order_type_rejected():
    a, _ = make_alpaca(alpaca_routes())
    with pytest.raises(VenueError):
        a.place_order("AAPL", "buy", 1, order_type="trailing_stop")


# ---- rounding -------------------------------------------------------------------

def test_price_rounding_to_penny_tick():
    assert AlpacaEquitiesAdapter.round_tick(123.4) == 123.4
    assert AlpacaEquitiesAdapter.round_tick(123.456, "down") == 123.45
    assert AlpacaEquitiesAdapter.round_tick(123.451, "up") == 123.46
    a, t = make_alpaca(alpaca_routes())
    a.place_order("AAPL", "buy", 1, order_type="limit", limit_price=123.456)
    assert _orders(t)[0]["limit_price"] == "123.45"  # generic limit: down


def test_qty_floors_to_whole_shares():
    a, t = make_alpaca(alpaca_routes())
    a.place_order("AAPL", "buy", 10.9)
    assert _orders(t)[0]["qty"] == "10"
    with pytest.raises(VenueError):
        a.place_order("AAPL", "buy", 0.4)  # floors to 0


def test_stop_trigger_and_tp_round_safe_per_side():
    """long SL down / short SL up (never late); long TP up / short TP down."""
    a, t = make_alpaca(alpaca_routes())
    a.place_venue_stop("AAPL", "long", 99.989)
    a.place_venue_stop("AAPL", "short", 100.011)
    stops = [b for b in _orders(t) if b["type"] == "stop"]
    assert stops[0]["stop_price"] == "99.98"
    assert stops[0]["side"] == "sell"
    assert stops[1]["stop_price"] == "100.02"
    assert stops[1]["side"] == "buy"
    a.place_venue_tp("AAPL", "long", 5, 110.004)
    a.place_venue_tp("AAPL", "short", 5, 110.006)
    tps = [b for b in _orders(t) if b["type"] == "limit"]
    assert tps[0]["limit_price"] == "110.01" and tps[0]["side"] == "sell"
    assert tps[1]["limit_price"] == "110.00" and tps[1]["side"] == "buy"
    assert all(b["time_in_force"] == "gtc" for b in tps)
    assert all(b["client_order_id"].startswith("KAOS-T") for b in tps)


# ---- close-only semantics (no reduce-only flag on equities) ----------------------

def test_reduce_only_flat_is_already_closed_skip():
    a, t = make_alpaca(alpaca_routes(positions=[]))
    res = a.place_market_order("AAPL", "sell", 5, reduce_only=True)
    assert res == {"ok": True, "order_id": None, "status": "ALREADY_CLOSED",
                   "avg_price": None, "qty_used": 5.0, "bumped": False,
                   "skipped": "already_closed"}
    assert not _post_urls(t)  # nothing sent


def test_reduce_only_clamps_to_real_position():
    a, t = make_alpaca(alpaca_routes())
    res = a.place_market_order("AAPL", "sell", 50, reduce_only=True)
    assert res["ok"] is True and res["qty_used"] == 5.0
    assert _orders(t)[-1]["qty"] == "5"


def test_reduce_only_sub_share_is_dust_skip():
    a, t = make_alpaca(alpaca_routes(
        positions=[dict(POS_AAPL_LONG, qty="0.4")]))
    res = a.place_market_order("AAPL", "sell", 1, reduce_only=True)
    assert res["status"] == "DUST_SKIPPED" and res["skipped"] == "dust"
    assert not _post_urls(t)


def test_reduce_only_side_mismatch_raises():
    a, _ = make_alpaca(alpaca_routes())
    with pytest.raises(VenueError):
        a.place_market_order("AAPL", "buy", 5, reduce_only=True)  # would ADD


# ---- entry sizing / skip envelopes ------------------------------------------------

def test_entry_sizing_wallet_fraction_slots_and_cash_clamp():
    a, t = make_alpaca(alpaca_routes(price=PRICE_100), sizing_pct=0.30,
                       slots=1)
    res = a.place_market_order("AAPL", "buy", 1)
    # wallet 50000 * 0.30 * 1.0 / 1 = 15000; cash clamp 40000*0.95 stays above
    # price 100 -> 150 shares
    assert res["ok"] is True and res["qty_used"] == 150.0
    assert res["bumped"] is True and res.get("skipped") is None
    assert _orders(t)[-1]["qty"] == "150"
    # slots split the SAME wallet budget: 50000*0.30/2 -> 75 shares
    a2, t2 = make_alpaca(alpaca_routes(price=PRICE_100), sizing_pct=0.30,
                         slots=2)
    res2 = a2.place_market_order("AAPL", "buy", 1)
    assert res2["qty_used"] == 75.0
    assert _orders(t2)[-1]["qty"] == "75"


def test_entry_margin_skip_when_target_below_one_share():
    a, t = make_alpaca(alpaca_routes(account=ACCOUNT_POOR,
                                     price={"trade": {"p": 20000.0}}),
                       sizing_pct=0.05)
    res = a.place_market_order("AAPL", "buy", 1)
    # 20000 * 0.05 = 1000 -> clamp avail 15000*0.95 -> 1000 -> 0.05 share -> 0
    assert res["status"] == "MARGIN_SKIPPED" and res["skipped"] == "margin"
    assert not _post_urls(t)


def test_entry_notional_cap_skip():
    a, t = make_alpaca(alpaca_routes(price=PRICE_100), max_notional_usdt=100.0)
    res = a.place_market_order("AAPL", "buy", 10)
    assert res["status"] == "NOTIONAL_CAP_SKIPPED"
    assert res["skipped"] == "notional_cap"
    assert not _post_urls(t)


def test_entry_min_notional_skip():
    a, t = make_alpaca(alpaca_routes(price={"trade": {"p": 1.0}}))
    res = a.place_market_order("AAPL", "buy", 0.5)
    assert res["status"] == "MIN_NOTIONAL_SKIPPED"
    assert res["skipped"] == "min_notional"
    assert not _post_urls(t)


# ---- PDT gate ----------------------------------------------------------------------

def test_entry_pdt_blocked_under_25k_when_counter_full(tmp_path):
    counter = PdtCounter(tmp_path / "pdt.json")
    now = _now_ms()
    for _ in range(3):
        counter.record_fill(now)
    a, t = make_alpaca(alpaca_routes(account=ACCOUNT_POOR),
                       pdt_counter=counter)
    res = a.place_market_order("AAPL", "buy", 1)
    assert res["status"] == "PDT_BLOCKED" and res["skipped"] == "pdt"
    assert not _post_urls(t)


def test_entry_pdt_allowed_when_equity_rich_even_with_full_counter(tmp_path):
    counter = PdtCounter(tmp_path / "pdt.json")
    now = _now_ms()
    for _ in range(3):
        counter.record_fill(now)
    a, t = make_alpaca(alpaca_routes(account=ACCOUNT_RICH, price=PRICE_100),
                       pdt_counter=counter)
    res = a.place_market_order("AAPL", "buy", 1)
    assert res["ok"] is True and res.get("skipped") is None  # >= $25k: gate OFF
    assert _post_urls(t)


def test_entry_pdt_unreadable_equity_gates_on_safe_side(tmp_path):
    """Account read fails -> equity unknown -> gate ON -> full counter blocks
    BEFORE any order is attempted (404 routes everywhere = unreadable)."""
    counter = PdtCounter(tmp_path / "pdt.json")
    now = _now_ms()
    for _ in range(3):
        counter.record_fill(now)
    a, t = make_alpaca([], pdt_counter=counter)  # empty routes -> all 404
    res = a.place_market_order("AAPL", "buy", 1)
    assert res["status"] == "PDT_BLOCKED"
    assert not _post_urls(t)


def test_successful_entry_records_day_trade(tmp_path):
    counter = PdtCounter(tmp_path / "pdt.json")
    a, _ = make_alpaca(alpaca_routes(account=ACCOUNT_POOR),
                       pdt_counter=counter)
    assert counter.can_open_trade(_now_ms()) is True
    a.place_market_order("AAPL", "buy", 1)
    assert counter.count_in_window(_now_ms()) == 1


# ---- honest inertness (no keys) ------------------------------------------------------

def test_no_keys_inert():
    t = FakeTransport(alpaca_routes())
    a = AlpacaEquitiesAdapter(transport=t)
    assert a.available is False
    assert a.host == PAPER_HOST
    with pytest.raises(VenueUnavailable):
        a.place_market_order("AAPL", "buy", 1)
    with pytest.raises(VenueUnavailable):
        a.place_market_order("AAPL", "sell", 1, reduce_only=True)
    with pytest.raises(VenueUnavailable):
        a.place_venue_stop("AAPL", "long", 99.0)
    with pytest.raises(VenueUnavailable):
        a.place_venue_tp("AAPL", "long", 1, 110.0)
    with pytest.raises(VenueUnavailable):
        a.place_order("AAPL", "buy", 1)
    assert len(t.calls) == 0  # inert executor never even dials
    assert a.connect_test() == (False, "no API keys")
    assert a.connect_note == "PAPER (no Alpaca keys)"


def test_funding_always_none():
    a, _ = make_alpaca(alpaca_routes())
    assert a.get_funding_rate("AAPL") is None


# ---- connect test / live gating --------------------------------------------------------

def test_connect_test_ok_and_failures():
    a, _ = make_alpaca(alpaca_routes())
    ok, detail = a.connect_test()
    assert ok is True and "equity=50000.00" in detail

    bad, _ = make_alpaca([("GET", "/v2/account", 401,
                           {"message": "invalid credentials"})])
    ok2, detail2 = bad.connect_test()
    assert ok2 is False and "401" in detail2
    assert "key123" not in detail2  # keys never leak into details


def test_from_env_live_requires_connect_test(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "key123")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "sec123")
    monkeypatch.setenv("KAOS_ALPACA_LIVE", "1")

    # connect-test FAILS on the live host -> stays paper, honestly
    transport = FakeTransport([("GET", "/v2/account", 401,
                                {"message": "denied"})])
    a = AlpacaEquitiesAdapter.from_env(transport=transport)
    assert a.host == PAPER_HOST and a.is_live is False
    assert "live gate failed" in a.connect_note

    # connect-test OK -> and ONLY then -> live host this boot
    transport2 = FakeTransport([("GET", "/v2/account", 200, ACCOUNT_RICH)])
    a2 = AlpacaEquitiesAdapter.from_env(transport=transport2)
    assert a2.host == LIVE_HOST and a2.is_live is True
    assert "connect-test OK" in a2.connect_note


def test_from_env_defaults_to_paper_without_live_flag(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "key123")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "sec123")
    monkeypatch.delenv("KAOS_ALPACA_LIVE", raising=False)
    a = AlpacaEquitiesAdapter.from_env(transport=FakeTransport([]))
    assert a.host == PAPER_HOST and a.is_live is False


def test_from_env_live_flag_without_keys_stays_paper(monkeypatch):
    monkeypatch.setenv("KAOS_ALPACA_LIVE", "1")
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    a = AlpacaEquitiesAdapter.from_env(transport=FakeTransport([]))
    assert a.available is False and a.is_live is False
    assert "no keys" in a.connect_note


# ---- reads / errors ---------------------------------------------------------------------

def test_balance_and_positions_mapping():
    a, t = make_alpaca(alpaca_routes())
    bal = a.get_balance()
    assert bal == {"wallet": 50000.0, "available": 40000.0,
                   "unrealized": 12.5, "equity": 50000.0,
                   "pdt_eligible": True}
    poor, _ = make_alpaca(alpaca_routes(account=ACCOUNT_POOR))
    assert poor.get_balance()["pdt_eligible"] is False
    pos = a.get_open_positions()[0]
    assert pos == {"symbol": "AAPL", "contracts": 5.0, "side": "long",
                   "qty": 5.0, "entry_price": 100.10, "mark_price": 101.00,
                   "unrealized": 4.50, "notional": 505.0, "leverage": 1.0,
                   "isolated": False}
    short, _ = make_alpaca(alpaca_routes(positions=[dict(
        POS_AAPL_LONG, qty="-2")]))
    s = short.get_open_positions()[0]
    assert s["side"] == "short" and s["contracts"] == 2.0
    assert a.get_price("AAPL") == 101.25
    assert any("data.alpaca.markets" in c["url"] and "trades:latest"
               in c["url"] for c in t.calls)


def test_http_error_maps_to_venue_error_without_retry_and_masks_key():
    a, t = make_alpaca([("POST", "/v2/orders", 422,
                         {"message": "insufficient key123 balance"})])
    with pytest.raises(VenueError) as ei:
        a.place_market_order("AAPL", "buy", 1)
    assert ei.value.code == 422
    assert "key123" not in ei.value.msg
    assert len([c for c in t.calls if c["method"] == "POST"]) == 1  # no retry


def test_429_registers_banned_until_and_gate_blocks_further_calls():
    a, t = make_alpaca([("POST", "/v2/orders", 429,
                         {"message": "rate limit"})])
    with pytest.raises(VenueError):
        a.place_market_order("AAPL", "buy", 1)
    assert a.banned_until_ms() > 0
    assert a.is_banned() is True
    n_before = len(t.calls)
    with pytest.raises(VenueError) as ei:
        a.place_market_order("AAPL", "buy", 1)
    assert ei.value.code == 429
    assert len(t.calls) == n_before  # gated at the door, no HTTP attempt


def test_5xx_retries_then_network_error_envelope():
    a, t = make_alpaca([("POST", "/v2/orders", 502, {"message": "bad gw"})])
    from entropy.venues import alpaca_equities as mod
    orig = mod.time.sleep
    mod.time.sleep = lambda _s: None  # shrink retry backoff for the test
    try:
        with pytest.raises(VenueError) as ei:
            a.place_market_order("AAPL", "buy", 1)
    finally:
        mod.time.sleep = orig
    assert ei.value.code is None
    assert len([c for c in t.calls if c["method"] == "POST"]) == 2  # tries=2


# ---- bracket bookkeeping -----------------------------------------------------------------

def test_adopt_open_stop_picks_closing_side_stop_only():
    a, _ = make_alpaca(alpaca_routes())
    assert a.adopt_open_stop("AAPL", "long") == "stop-9"
    # short position closing side is buy -> the sell stop must NOT match
    a2, _ = make_alpaca(alpaca_routes(orders=[OPEN_STOP_ROW]))
    assert a2.adopt_open_stop("AAPL", "short") is None


def test_adopt_open_tp_prefers_kaos_prefix():
    a, _ = make_alpaca(alpaca_routes())
    assert a.adopt_open_tp("AAPL", "long") == "tp-7"
    a2, _ = make_alpaca(alpaca_routes(
        orders=[{"id": "tp-7", "symbol": "AAPL", "side": "sell",
                 "type": "limit", "status": "open",
                 "client_order_id": "manual", "qty": "5"}]))
    assert a2.adopt_open_tp("AAPL", "long") == "tp-7"  # closing-side fallback


def test_cancel_tolerates_already_gone_404():
    a = AlpacaEquitiesAdapter(
        api_key_id="k", api_secret="s",
        transport=FakeTransport([("DELETE", "/v2/orders/", 404,
                                  {"message": "gone"})]))
    a._stop_orders["AAPL"] = "stop-1"
    assert a.cancel_venue_stop("AAPL") is True
    a._tp_orders["AAPL"] = "tp-1"
    assert a.cancel_venue_tp("AAPL") is True
    assert not a._stop_orders and not a._tp_orders


def test_sweep_cancels_only_opposite_of_new_position_side():
    """Crypto parity: sweep(sym, 'short') removes SELL stops (an old long's
    closePosition-analogue stop must never touch the new short)."""
    a, t = make_alpaca(alpaca_routes())
    assert a.sweep_stale_stops("AAPL", "short") == 1
    deletes = [c["url"] for c in t.calls if c["method"] == "DELETE"]
    assert f"{PAPER_HOST}/v2/orders/stop-9" in deletes
    # new long -> want_cancel 'buy' -> the sell stop SURVIVES
    a2, t2 = make_alpaca(alpaca_routes())
    assert a2.sweep_stale_stops("AAPL", "long") == 0
    assert not [c for c in t2.calls if c["method"] == "DELETE"]


def test_alive_status_mapping():
    a, _ = make_alpaca([("GET", "/v2/orders/oid-new", 200,
                         {"id": "oid-new", "status": "partially_filled"})])
    assert a.order_alive("AAPL", "oid-new") is True
    assert a.algo_order_alive("AAPL", "oid-new") is True
    dead, _ = make_alpaca([("GET", "/v2/orders/oid-new", 200,
                            {"id": "oid-new", "status": "filled"})])
    assert dead.order_alive("AAPL", "oid-new") is False
    err, _ = make_alpaca([("GET", "/v2/orders/oid-new", 404,
                           {"message": "gone"})])
    assert err.order_alive("AAPL", "oid-new") is False  # failure -> False


def test_place_venue_stop_without_position_is_honest_none():
    a, t = make_alpaca(alpaca_routes(positions=[]))
    assert a.place_venue_stop("AAPL", "long", 99.0) is None
    assert not _post_urls(t)
    assert any("no open position" in e for e in a.last_errors)


# ---- helper ---------------------------------------------------------------------------------

def _now_ms() -> int:
    """The adapter's own clock is time.time() — tests must use the SAME clock
    for counter fills/gate checks so the suite is date-independent."""
    import time

    return int(time.time() * 1000)
