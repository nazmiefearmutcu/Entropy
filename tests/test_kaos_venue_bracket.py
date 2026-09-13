"""2026-09-08 KAOS venue-bracket kampanyası — offline unit testler.

Kapsam: reduce-only LIMIT TP yerleşimi, açık emir adoptu (openAlgoOrders /
openOrders), -4130 ALREADY_CLOSED, close-first bracket iptal sıralaması,
serbest-marj klempi + slot-bölüşümlü boyutlandırma ve runner tarafında
hayalet (fantom) kâğıt pozisyonların shadow-exit'i.
Hiçbir test ağa çıkmaz: HTTP katmanı mock'lanır.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kaos_testnet_exec as kx  # noqa: E402
from kaos_testnet_exec import ExecutorError, TestnetExecutor  # noqa: E402


# ---- helpers ----------------------------------------------------------------

def _ex(resp_map: dict | None = None) -> tuple[TestnetExecutor, list]:
    """Executor whose _request returns resp_map[(method, path)] or the
    single default; every call is captured."""
    ex = TestnetExecutor("key123", "sec123")
    captured: list = []
    resp_map = resp_map or {}

    def fake_request(method, path, params, tries=2, _resynced=False):
        captured.append((method, path, dict(params)))
        if (method, path) in resp_map:
            v = resp_map[(method, path)]
        else:
            for (m, p), v in resp_map.items():
                if m == method and p.endswith(path):
                    break
            else:
                v = resp_map.get("default", {})
        if isinstance(v, Exception):
            raise v
        return v

    ex._request = fake_request  # type: ignore[assignment]
    ex.captured = captured  # type: ignore[attr-defined]
    return ex, captured


STOP_ROW = {"algoId": 3000002180929115, "orderType": "STOP_MARKET",
            "algoStatus": "NEW", "symbol": "ETHUSDT", "side": "SELL",
            "triggerPrice": "2419.14"}
TP_ROW = {"orderId": 555, "type": "LIMIT", "side": "SELL",
          "reduceOnly": "true", "status": "NEW", "symbol": "ETHUSDT"}


# ---- adopt: open algo stops -------------------------------------------------

def test_adopt_open_stop_picks_stop_market_and_tracks():
    ex, _ = _ex({("GET", "/fapi/v1/openAlgoOrders"): [STOP_ROW]})
    aid = ex.adopt_open_stop("ETHUSDT")
    assert aid == "3000002180929115"
    assert ex._stop_orders["ETHUSDT"] == {"id": aid, "algo": True}


def test_adopt_open_stop_ignores_non_stop_rows():
    ex, _ = _ex({("GET", "/fapi/v1/openAlgoOrders"):
                 [{"algoId": 1, "orderType": "TAKE_PROFIT_MARKET"}]})
    assert ex.adopt_open_stop("ETHUSDT") is None
    assert "ETHUSDT" not in ex._stop_orders


def test_adopt_open_stop_fail_open_on_error():
    ex, _ = _ex({("GET", "/fapi/v1/openAlgoOrders"):
                 ExecutorError(-1003, "banned")})
    assert ex.adopt_open_stop("ETHUSDT") is None


def test_adopt_open_tp_picks_reduce_only_limit():
    ex, _ = _ex({("GET", "/fapi/v1/openOrders"): [TP_ROW]})
    oid = ex.adopt_open_tp("ETHUSDT", "long")
    assert oid == "555"
    assert ex._tp_orders["ETHUSDT"] == {"id": "555", "algo": False}


def test_adopt_open_tp_requires_matching_side():
    ex, _ = _ex({("GET", "/fapi/v1/openOrders"): [TP_ROW]})
    assert ex.adopt_open_tp("ETHUSDT", "short") is None  # TP_ROW SELL


# ---- venue TP ---------------------------------------------------------------

def test_place_venue_tp_params_reduce_only_limit_gtc():
    ex, cap = _ex({("POST", "/fapi/v1/order"): {"orderId": 9,
                                                "status": "NEW"}})
    ex._lot_loaded = True
    ex._tick_size = {"ETHUSDT": 0.01}
    ex._lot_step = {"ETHUSDT": 0.001}
    oid = ex.place_venue_tp("ETHUSDT", "long", 0.009, 2512.2689)
    method, path, params = cap[-1]
    assert (method, path) == ("POST", "/fapi/v1/order")
    assert params["type"] == "LIMIT" and params["timeInForce"] == "GTC"
    assert params["reduceOnly"] == "true" and params["side"] == "SELL"
    # long TP YUKARI yuvarlanır (tick gridine) — piyasanın üstünde kalsın
    assert params["price"] == pytest.approx(2512.27)
    assert params["quantity"] == "0.009"
    assert oid == "9"
    assert ex._tp_orders["ETHUSDT"]["id"] == "9"


def test_place_venue_tp_short_side_and_down_rounding():
    ex, cap = _ex({("POST", "/fapi/v1/order"): {"orderId": 10}})
    ex._lot_loaded = True
    ex._tick_size = {"ETHUSDT": 0.01}
    ex.place_venue_tp("ETHUSDT", "short", 1.0, 2400.123)
    _, _, params = cap[-1]
    assert params["side"] == "BUY"
    assert params["price"] == pytest.approx(2400.12)


def test_place_venue_tp_refuses_zero_qty_or_price():
    ex, cap = _ex()
    assert ex.place_venue_tp("ETHUSDT", "long", 0.0, 2500.0) is None
    assert ex.place_venue_tp("ETHUSDT", "long", 1.0, 0.0) is None
    assert not cap


def test_order_alive_accepts_open_statuses():
    ex, _ = _ex({("GET", "/fapi/v1/order"): {"status": "NEW"}})
    assert ex.order_alive("ETHUSDT", "555") is True
    ex2, _ = _ex({("GET", "/fapi/v1/order"): {"status": "FILLED"}})
    assert ex2.order_alive("ETHUSDT", "555") is False


# ---- close-first bracket ordering + -4130 -----------------------------------

def test_successful_close_cancels_stop_and_tp_after():
    ex, cap = _ex({("POST", "/fapi/v1/order"): {"orderId": 7,
                                                "status": "FILLED"}})
    ex._stop_orders["ETHUSDT"] = {"id": "111", "algo": True}
    ex._tp_orders["ETHUSDT"] = {"id": "555", "algo": False}
    out = ex.place_market_order("ETHUSDT", "SELL", 0.009, reduce_only=True)
    assert out["ok"] is True and out["status"] == "FILLED"
    deletes = [c for c in cap if c[0] == "DELETE"]
    paths = [c[1] for c in deletes]
    assert paths == ["/fapi/v1/algoOrder", "/fapi/v1/order"]  # stop + TP
    assert "ETHUSDT" not in ex._stop_orders
    assert "ETHUSDT" not in ex._tp_orders


def test_failed_close_keeps_bracket_orders():
    """Kapanış emri düşerse stop/TP YERİNDE kalmalı (eski cancel-first düzeni
    pozisyonu çıplak bırakıyordu)."""
    ex, cap = _ex({("POST", "/fapi/v1/order"):
                   ExecutorError(None, "network unreachable")})
    ex._stop_orders["ETHUSDT"] = {"id": "111", "algo": True}
    with pytest.raises(ExecutorError):
        ex.place_market_order("ETHUSDT", "SELL", 0.009, reduce_only=True)
    assert not [c for c in cap if c[0] == "DELETE"]
    assert "ETHUSDT" in ex._stop_orders


def test_dust_skip_keeps_bracket_orders():
    """-4164 toz kapanışta pozisyon DURUYOR → stop/TP çekilmez."""
    ex, cap = _ex({("POST", "/fapi/v1/order"):
                   ExecutorError(-4164, "Order's notional must be no smaller")})
    ex._stop_orders["ETHUSDT"] = {"id": "111", "algo": True}
    out = ex.place_market_order("ETHUSDT", "SELL", 0.001, reduce_only=True)
    assert out["skipped"] == "dust"
    assert not [c for c in cap if c[0] == "DELETE"]
    assert "ETHUSDT" in ex._stop_orders


def test_4130_with_flat_position_is_already_closed(monkeypatch):
    ex, cap = _ex({("POST", "/fapi/v1/order"):
                   ExecutorError(-4130, "reduceOnly order rejected")})
    monkeypatch.setattr(ex, "_position_flat", lambda sym: True)
    ex._stop_orders["ETHUSDT"] = {"id": "111", "algo": True}
    out = ex.place_market_order("ETHUSDT", "SELL", 0.009, reduce_only=True)
    assert out["ok"] is True and out["status"] == "ALREADY_CLOSED"
    assert out["skipped"] == "already_closed"
    assert [c for c in cap if c[0] == "DELETE"]   # bracket temizlenir
    assert "ETHUSDT" not in ex._stop_orders


def test_4130_with_open_position_is_an_error(monkeypatch):
    """Pozisyon hâlâ duruyorsa -4130 başarı SAYILMAZ (iz düşülmez)."""
    ex, _ = _ex({("POST", "/fapi/v1/order"):
                 ExecutorError(-4130, "reduceOnly order rejected")})
    monkeypatch.setattr(ex, "_position_flat", lambda sym: False)
    with pytest.raises(ExecutorError):
        ex.place_market_order("ETHUSDT", "SELL", 0.009, reduce_only=True)


def test_position_flat_true_when_symbol_absent():
    ex, _ = _ex({("GET", "/fapi/v2/positionRisk"):
                 [{"symbol": "BTCUSDT", "positionAmt": "1.0"}]})
    assert ex._position_flat("ETHUSDT") is True
    assert ex._position_flat("BTCUSDT") is False
    # doğrulama çağrısı patlarsa güvenli taraf: 'düz değil'
    ex2, _ = _ex({("GET", "/fapi/v2/positionRisk"):
                  ExecutorError(None, "network unreachable")})
    assert ex2._position_flat("ETHUSDT") is False


# ---- sizing: margin clamp + slot split --------------------------------------

def test_sizing_splits_budget_across_slots(monkeypatch):
    ex = TestnetExecutor("k", "s", leverage=10, sizing_pct=0.25, slots=3)
    ex._lot_loaded = True
    ex._lot_step = {"ETHUSDT": 0.001}
    monkeypatch.setattr(ex, "get_balance",
                        lambda: {"wallet": 100.0, "available": 50.0,
                                 "unrealized": 0.0})
    monkeypatch.setattr(ex, "get_price", lambda sym: 2500.0)
    monkeypatch.setattr(ex, "_request",
                        lambda m, p, params, tries=2, _resynced=False:
                        {"orderId": 1, "status": "FILLED", "avgPrice": "2500"})
    out = ex.place_market_order("ETHUSDT", "BUY", 0.001)
    # hedef = 100*0.25*10/3 = 83.33 USDT → 0.033 ETH (step 0.001'e yuvarlanır)
    assert out["qty_used"] == pytest.approx(0.033)
    assert out["ok"] is True


def test_margin_clamp_shrinks_to_available():
    ex = TestnetExecutor("k", "s", leverage=10, sizing_pct=0.25, slots=1)
    ex._lot_loaded = True
    ex._lot_step = {"ETHUSDT": 0.001}
    # wallet 100 → hedef 250 USDT; available 5 → klemp 5*10*0.95 = 47.5 USDT
    ex.get_balance = lambda: {"wallet": 100.0, "available": 5.0,
                              "unrealized": 0.0}  # type: ignore[method-assign]
    ex.get_price = lambda sym: 2500.0  # type: ignore[method-assign]
    captured: list = []

    def fake_req(m, p, params, tries=2, _resynced=False):
        captured.append((m, p, dict(params)))
        return {"orderId": 1, "status": "FILLED", "avgPrice": "2500"}

    ex._request = fake_req  # type: ignore[assignment]
    out = ex.place_market_order("ETHUSDT", "BUY", 0.001)
    assert out["ok"] is True
    assert out["qty_used"] == pytest.approx(0.019)  # 47.5/2500, step'e yuvarlu


def test_margin_skip_when_below_min_notional():
    ex = TestnetExecutor("k", "s", leverage=10, sizing_pct=0.25, slots=1)
    ex._lot_loaded = True
    ex._lot_step = {"ETHUSDT": 0.001}
    # available 0.5 → klemp 4.75 USDT < 20 USDT min → emir HİÇ açılmaz
    ex.get_balance = lambda: {"wallet": 100.0, "available": 0.5,
                              "unrealized": 0.0}  # type: ignore[method-assign]
    ex.get_price = lambda sym: 2500.0  # type: ignore[method-assign]
    captured: list = []

    def fake_req(m, p, params, tries=2, _resynced=False):
        captured.append((m, p, dict(params)))
        return {"orderId": 1, "status": "FILLED", "avgPrice": "2500"}

    ex._request = fake_req  # type: ignore[assignment]
    out = ex.place_market_order("ETHUSDT", "BUY", 0.001)
    assert out["status"] == "MARGIN_SKIPPED"
    assert out["skipped"] == "margin"
    assert not [c for c in captured if c[1] == "/fapi/v1/order"]


# ---- env: slots parsing ------------------------------------------------------

def test_make_executor_slots_env(monkeypatch):
    monkeypatch.setenv("KAOS_EXCHANGE_NETWORK", "mainnet")
    monkeypatch.setenv("BINANCE_API_KEY", "k" * 30)
    monkeypatch.setenv("BINANCE_API_SECRET", "s" * 30)
    monkeypatch.setenv("KAOS_EXCHANGE_SLOTS", "3")
    ex = kx.make_executor_from_env()
    assert ex is not None and ex.slots == 3
    monkeypatch.setenv("KAOS_EXCHANGE_SLOTS", "garbage")
    ex2 = kx.make_executor_from_env()
    assert ex2 is not None and ex2.slots == 1


# ---- short-açılımı: taraf-farkında adopt + karşı-taraf süpürme ---------------

def test_adopt_open_stop_is_side_aware():
    """SELL stopu LONG pozisyona adopt edilir ama SHORT'a EDİLMEZ — aksi halde
    short stopsuz kalırdı (2026-09-08 shorts-on)."""
    ex, _ = _ex({("GET", "/fapi/v1/openAlgoOrders"): [STOP_ROW]})  # SELL stop
    assert ex.adopt_open_stop("ETHUSDT", "long") == "3000002180929115"
    ex2, _ = _ex({("GET", "/fapi/v1/openAlgoOrders"): [STOP_ROW]})
    assert ex2.adopt_open_stop("ETHUSDT", "short") is None
    assert "ETHUSDT" not in ex2._stop_orders


BUY_STOP_ROW = {"algoId": 777, "orderType": "STOP_MARKET", "side": "BUY",
                "algoStatus": "NEW", "triggerPrice": "2600.0"}


def test_sweep_stale_stops_cancels_opposite_side_only():
    """Long girişi öncesi eski SHORT'un BUY stopları silinir; SELL stoplara
    dokunulmaz."""
    ex, cap = _ex({("GET", "/fapi/v1/openAlgoOrders"):
                   [BUY_STOP_ROW, STOP_ROW]})
    n = ex.sweep_stale_stops("ETHUSDT", "long")
    assert n == 1
    deletes = [c for c in cap if c[0] == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0][2]["algoId"] == 777


def test_place_venue_stop_sweeps_stale_first():
    ex, cap = _ex({("POST", "/fapi/v1/algoOrder"):
                   {"orderId": 999, "algoId": 999},
                   ("GET", "/fapi/v1/openAlgoOrders"): [BUY_STOP_ROW]})
    ex._lot_loaded = True
    ex._tick_size = {"ETHUSDT": 0.01}
    oid = ex.place_venue_stop("ETHUSDT", "long", 2419.14)
    assert oid == "999"
    deletes = [c for c in cap if c[0] == "DELETE"]
    assert len(deletes) == 1 and deletes[0][2]["algoId"] == 777


# ---- giriş telemetrisi (2026-09-08 "neden işlem yok?" sorusuna veri) ---------

def test_entry_telemetry_reject_contract():
    """Redd kaydı: kapı-adı sayacı büyür, run-control ön-ek'i kapı adına
    ayrışır, zaman damgası yazılır."""
    import entropy_live_paper as _lp
    obj = _lp.LivePaper.__new__(_lp.LivePaper)   # __init__'siz sözleşme testi
    obj.entry_telemetry = {"bars_fed": 0, "enter_signals_seen": 0,
                           "entries_allowed": 0, "rejected": {},
                           "last_enter_signal_utc": None,
                           "last_reject_utc": None}
    _lp.LivePaper._telemetry_reject(obj, "cooldown")
    _lp.LivePaper._telemetry_reject(obj, "cooldown")
    _lp.LivePaper._telemetry_reject(obj, "run-control:paused")
    assert obj.entry_telemetry["rejected"]["cooldown"] == 2
    assert obj.entry_telemetry["rejected"]["run-control"] == 1
    assert obj.entry_telemetry["last_reject_utc"] is not None
