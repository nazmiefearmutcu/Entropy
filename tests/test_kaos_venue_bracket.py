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
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kaos_testnet_exec as kx  # noqa: E402
import entropy_live_paper as _lp  # noqa: E402
from entropy.bot.portfolio import PositionSide  # noqa: E402
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


# ---- LIQ-CLAMP (2026-09-10: SL asla likidasyon fiyatını aşamaz) --------------

def _posrisk_row(sym: str, side: str, liq: float) -> dict:
    return {"symbol": sym, "positionAmt": 1.0 if side == "long" else -1.0,
            "liquidationPrice": liq, "entryPrice": 100.0,
            "markPrice": 100.0}


def _stop_post(cap: list) -> dict:
    posts = [c for c in cap
             if c[0] == "POST" and c[1] == "/fapi/v1/algoOrder"]
    assert posts, "no algoOrder POST captured"
    return posts[0][2]


def test_place_venue_stop_liq_clamps_long_beyond_liq():
    ex, cap = _ex({("POST", "/fapi/v1/algoOrder"):
                   {"orderId": 1, "algoId": 1},
                   ("GET", "/fapi/v1/openAlgoOrders"): [],
                   ("GET", "/fapi/v2/positionRisk"):
                   [_posrisk_row("ETHUSDT", "long", 2200.0)]})
    ex._lot_loaded = True
    oid = ex.place_venue_stop("ETHUSDT", "long", 2000.0)   # 20σ stopu liq altında
    assert oid == "1"
    assert float(_stop_post(cap)["triggerPrice"]) == pytest.approx(2222.0)


def test_place_venue_stop_liq_clamps_short_beyond_liq():
    ex, cap = _ex({("POST", "/fapi/v1/algoOrder"):
                   {"orderId": 2, "algoId": 2},
                   ("GET", "/fapi/v1/openAlgoOrders"): [],
                   ("GET", "/fapi/v2/positionRisk"):
                   [_posrisk_row("ETHUSDT", "short", 100.0)]})
    ex._lot_loaded = True
    oid = ex.place_venue_stop("ETHUSDT", "short", 105.0)   # short stopu liq üstünde
    assert oid == "2"
    assert float(_stop_post(cap)["triggerPrice"]) == pytest.approx(99.0)


def test_place_venue_stop_untouched_when_inside_liq():
    ex, cap = _ex({("POST", "/fapi/v1/algoOrder"):
                   {"orderId": 3, "algoId": 3},
                   ("GET", "/fapi/v1/openAlgoOrders"): [],
                   ("GET", "/fapi/v2/positionRisk"):
                   [_posrisk_row("ETHUSDT", "long", 2200.0)]})
    ex._lot_loaded = True
    ex._tick_size = {"ETHUSDT": 0.01}
    ex.place_venue_stop("ETHUSDT", "long", 2300.0)   # liq'in üstünde = güvenli
    assert float(_stop_post(cap)["triggerPrice"]) == pytest.approx(2300.0)


def test_place_venue_stop_liq_fetch_fails_open():
    """Liq sorgusu düşse bile stop kurulur (koruma yokluktan iyidir)."""
    ex, cap = _ex({("POST", "/fapi/v1/algoOrder"):
                   {"orderId": 4, "algoId": 4},
                   ("GET", "/fapi/v1/openAlgoOrders"): [],
                   ("GET", "/fapi/v2/positionRisk"): ExecutorError(-1, "net down")})
    ex._lot_loaded = True
    oid = ex.place_venue_stop("ETHUSDT", "long", 2419.14)
    assert oid == "4"
    assert float(_stop_post(cap)["triggerPrice"]) == pytest.approx(2419.14)


def test_place_venue_stop_clamp_rounds_away_from_liq():
    """Klemp sonrası tick yuvarlaması liq buffer'ını DELMEMELİ: long floor
    tick'e YUKARI yuvarlanır (ETH tick 0.01 → 2218.862 → 2218.87)."""
    ex, cap = _ex({("POST", "/fapi/v1/algoOrder"):
                   {"orderId": 5, "algoId": 5},
                   ("GET", "/fapi/v1/openAlgoOrders"): [],
                   ("GET", "/fapi/v2/positionRisk"):
                   [_posrisk_row("ETHUSDT", "long", 2196.9)]})
    ex._lot_loaded = True
    ex._tick_size = {"ETHUSDT": 0.01}
    ex.place_venue_stop("ETHUSDT", "long", 2000.0)
    # 2196.9*1.01 = 2218.869 → tick'e yukarı 2218.87 (asla aşağı değil)
    assert float(_stop_post(cap)["triggerPrice"]) == pytest.approx(2218.87)


def test_get_open_positions_exposes_liquidation_price():
    ex, _ = _ex({("GET", "/fapi/v2/positionRisk"):
                 [{"symbol": "ETHUSDT", "positionAmt": "2.0",
                   "liquidationPrice": "2100.5", "entryPrice": "2500",
                   "markPrice": "2450", "unrealizedProfit": "-1.0",
                   "notional": "500", "leverage": "10",
                   "marginType": "isolated"}]})
    rows = ex.get_open_positions()
    assert rows and rows[0]["liquidation_price"] == pytest.approx(2100.5)


# ---- 2026-09-11 CANLI FIX: marginType -4067 quirk (giriş kaybı) --------------
# Kanıt: NEARUSDT/FILUSDT/APTUSDT flat + isolated; Binance setMarginType bu
# durumda -4067 (mesaj 'position side...') dönebiliyor; eski kod ölümcül
# sayıp mirror girişini iptal ediyordu (kâğıtta short, gerçek hesapta yok).

def test_ensure_lev_isolated_skips_post_when_already_isolated():
    ex, cap = _ex({
        ("GET", "/fapi/v2/positionRisk"):
        [{"symbol": "NEARUSDT", "positionAmt": "0.0",
          "marginType": "isolated"}],
        ("POST", "/fapi/v1/leverage"): {"leverage": 10},
    })
    ex.leverage = 10
    ex._ensure_lev_isolated("NEARUSDT")
    assert not [c for c in cap if c[1] == "/fapi/v1/marginType"]
    assert [c for c in cap if c[1] == "/fapi/v1/leverage"]
    assert "NEARUSDT" in ex._lev_done


def test_ensure_lev_isolated_4067_verified_isolated_is_ok():
    ex, cap = _ex()
    state = {"reads": 0}

    def fake(m, p, params, tries=2, _resynced=False):
        cap.append((m, p, dict(params)))
        if p == "/fapi/v2/positionRisk":
            state["reads"] += 1
            mt = "cross" if state["reads"] == 1 else "isolated"
            return [{"symbol": "NEARUSDT", "positionAmt": "0.0",
                     "marginType": mt}]
        if p == "/fapi/v1/marginType":
            raise ExecutorError(
                -4067, "Position side cannot be changed if there exists "
                       "open orders.")
        if p == "/fapi/v1/leverage":
            return {"leverage": 10}
        return {}

    ex._request = fake  # type: ignore[assignment]
    ex.leverage = 10
    ex._ensure_lev_isolated("NEARUSDT")      # raise ETMEMELİ (quirk)
    assert "NEARUSDT" in ex._lev_done


def test_ensure_lev_isolated_4067_still_cross_raises():
    ex, cap = _ex()

    def fake(m, p, params, tries=2, _resynced=False):
        cap.append((m, p, dict(params)))
        if p == "/fapi/v2/positionRisk":
            return [{"symbol": "NEARUSDT", "positionAmt": "0.0",
                     "marginType": "cross"}]
        if p == "/fapi/v1/marginType":
            raise ExecutorError(-4067, "Position side cannot be changed")
        return {}

    ex._request = fake  # type: ignore[assignment]
    ex.leverage = 10
    with pytest.raises(ExecutorError):
        ex._ensure_lev_isolated("NEARUSDT")
    assert "NEARUSDT" not in ex._lev_done


# ---- 2026-09-11 venue çıkış kanıtı (LINK yanlış 'mirror failed' vakası) ------
# Kanıt: LINK girişi 9.79@11.449 doldu, TP 11.400'den kapandı (+$0.48);
# reconcile izi temizlediği için eski kod kaydı 'mirror failed' sanıyordu.

def test_order_status_and_algo_order_filled():
    ex, _ = _ex({("GET", "/fapi/v1/order"): {"status": "FILLED"}})
    assert ex.order_status("LINKUSDT", "123") == "FILLED"
    ex2, _ = _ex({("GET", "/fapi/v1/algoOrder"):
                  {"algoStatus": "FINISHED", "actualOrderId": "999"}})
    assert ex2.algo_order_filled("LINKUSDT", "123") is True
    ex3, _ = _ex({("GET", "/fapi/v1/algoOrder"):
                  {"algoStatus": "CANCELED", "actualOrderId": ""}})
    assert ex3.algo_order_filled("LINKUSDT", "123") is False
    ex4, _ = _ex({("GET", "/fapi/v1/order"):
                  ExecutorError(None, "network")})
    assert ex4.order_status("LINKUSDT", "123") is None


def test_venue_close_evidence_tp_filled():
    class X:
        def order_status(self, sym, oid):
            return "FILLED"

        def algo_order_filled(self, sym, oid):
            return False

    obj = _lp.LivePaper.__new__(_lp.LivePaper)
    obj.exec_ = X()
    ev = _lp.LivePaper._venue_close_evidence(
        obj, "LINKUSDT", {"tp_id": "51982023383"})
    assert ev == {"how": "take_profit", "order_id": "51982023383"}


def test_venue_close_evidence_stop_filled():
    class X:
        def order_status(self, sym, oid):
            return "NEW"

        def algo_order_filled(self, sym, oid):
            return True

    obj = _lp.LivePaper.__new__(_lp.LivePaper)
    obj.exec_ = X()
    ev = _lp.LivePaper._venue_close_evidence(
        obj, "ETHUSDT", {"tp_id": "1", "stop_id": "2"})
    assert ev == {"how": "stop", "order_id": "2"}


def test_venue_close_evidence_none_when_unproven():
    class X:
        def order_status(self, sym, oid):
            return "NEW"

        def algo_order_filled(self, sym, oid):
            return False

    obj = _lp.LivePaper.__new__(_lp.LivePaper)
    obj.exec_ = X()
    assert _lp.LivePaper._venue_close_evidence(
        obj, "ETHUSDT", {"tp_id": "1", "stop_id": "2"}) is None


# ---- 2026-09-11 BE-ratchet closePosition tekliliği (sessiz churn biter) ------

def _ratchet_fixture() -> tuple:
    class X:
        def __init__(self):
            self.placed = 0

        def cancel_stop_by_id(self, sym, oid):
            return False

        def get_open_positions(self):
            return [{"symbol": "ARBUSDT", "side": "short",
                     "contracts": 10.0, "qty": 10.0,
                     "entry_price": 0.14288}]

        def place_venue_stop(self, sym, side, px):
            self.placed += 1
            return None          # -4130: closePosition stop zaten var

        def adopt_open_stop(self, sym, side):
            return "111"

    obj = _lp.LivePaper.__new__(_lp.LivePaper)
    obj.exec_ = X()
    obj._mirror_live = True
    obj.mirror_positions = {"ARBUSDT": {
        "side": "short", "qty": 10.0, "stop_id": "111", "sl_px": 0.16304}}
    obj.trail_on = False
    obj._last_ratchet_place = 0.0
    obj.last_close = {"ARBUSDT": 0.1390}
    obj._costs = {}
    obj.cfg = SimpleNamespace(fee_bps=10.0, slippage_bps=3.0)
    pos = SimpleNamespace(side=PositionSide.SHORT, entry_px=0.14288,
                          tp_px=0.13774, stop_px=0.16304,
                          symbol="binance-spot:ARBUSDT")
    obj.runner = SimpleNamespace(
        portfolio=SimpleNamespace(positions={"binance-spot:ARBUSDT": pos}))
    return obj, obj.mirror_positions["ARBUSDT"]


def test_venue_ratchet_marks_unsupported_and_adopts():
    obj, mp = _ratchet_fixture()
    _lp.LivePaper._venue_ratchet(obj, "bar")
    assert obj.exec_.placed == 1           # tek deneme
    assert mp.get("be_unsupported") is True
    assert mp.get("be_done") is True
    assert mp.get("stop_id") == "111"      # mevcut stop adopt edildi

    # ikinci pass: be_unsupported -> hiç deneme yok (churn bitti)
    _lp.LivePaper._venue_ratchet(obj, "bar")
    assert obj.exec_.placed == 1


# ---- 2026-09-11 collect_closed dürüst kanıt etiketi (integration) ------------

def _stage_round_trip(lp, sym, entry_ts_ns: int) -> None:
    lp.runner.portfolio.open(sym, PositionSide.LONG, 0.1, 100.0,
                             99.5, 101.5, entry_ts_ns, fee=0.01)
    entry = SimpleNamespace(symbol=sym, side=SimpleNamespace(value="buy"),
                            price=100.0, qty=0.1, fee=0.01,
                            ts_ns=entry_ts_ns)
    lp.ledger.fills.append((entry, SimpleNamespace(value="open")))
    lp.ledger.open_levels.append((99.5, 101.5))
    idx = len(lp.ledger.fills) - 1
    lp.open_fills[sym] = (idx, entry)
    lp.cursor = idx + 1
    exitf = SimpleNamespace(symbol=sym, side=SimpleNamespace(value="sell"),
                            price=101.5, qty=0.1, fee=0.01,
                            ts_ns=entry_ts_ns + 60_000_000_000)
    lp.ledger.fills.append((exitf, SimpleNamespace(value="take_profit")))
    lp.ledger.open_levels.append(None)


def test_collect_closed_venue_tp_fill_marks_verified(tmp_path):
    lp = _lp.LivePaper(["BTCUSDT"], 100.0, tmp_path)
    sym = lp.syms["BTCUSDT"]
    lp.exec_ = object()          # mirror katmanı açık; _mirror burada çağrılmaz
    lp._mirror_live = True
    lp.open_mirror[sym] = {"ok": True, "order_id": "51982023119",
                           "qty_used": 9.79, "bumped": True}
    _stage_round_trip(lp, sym, 1_789_082_000_000 * 1_000_000)
    lp._venue_closed["BTCUSDT"] = {"how": "take_profit",
                                   "order_id": "51982023383"}
    lp.collect_closed()
    rec = lp.closed_all[-1]
    assert rec["origin"] == "live"
    assert rec["exchange_verified"] is True
    assert rec["exchange_order_id"] == "51982023383"
    assert rec["exchange_exit_venue"] == "take_profit"
    assert "borsa tarafında" in rec["exchange_note"]
    assert not any("EXIT_SKIP" in e for e in lp._exec_errors)


def test_collect_closed_unproven_vanish_is_honest(tmp_path):
    lp = _lp.LivePaper(["BTCUSDT"], 100.0, tmp_path)
    sym = lp.syms["BTCUSDT"]
    lp.exec_ = object()
    lp._mirror_live = True
    lp.open_mirror[sym] = {"ok": True, "order_id": "111"}
    _stage_round_trip(lp, sym, 1_789_082_000_000 * 1_000_000)
    lp.collect_closed()
    rec = lp.closed_all[-1]
    assert rec["exchange_verified"] is False
    assert "çıkış aynası atlandı" in rec["exchange_note"]
    assert any("EXIT_SKIP" in e for e in lp._exec_errors)


def test_collect_closed_exit_rejected_but_venue_tp_filled(tmp_path):
    """2026-09-11: -2022 (pozisyon borsada zaten kapalı) alınsa bile venue TP
    doldu kanıtı varsa kayıt 'failed' değil 'borsada kapandı' olur (canlı
    ETHUSDT vakası: giriş 0.036@2500.53 → TP 0.036@2548.18, +$1.72)."""
    lp = _lp.LivePaper(["BTCUSDT"], 100.0, tmp_path)
    sym = lp.syms["BTCUSDT"]

    class X:
        stopped = False
        tped = False

        def place_market_order(self, symbol, side, qty, reduce_only=False):
            raise ExecutorError(-2022, "ReduceOnly Order is rejected.")

        def order_status(self, s, oid):
            return "FILLED"

        def algo_order_filled(self, s, oid):
            return False

        def cancel_venue_stop(self, s):
            self.stopped = True
            return True

        def cancel_venue_tp(self, s):
            self.tped = True
            return True

    ex = X()
    lp.exec_ = ex
    lp._mirror_live = True
    lp.open_mirror[sym] = {"ok": True, "order_id": "111", "qty_used": 0.1}
    lp.mirror_positions["BTCUSDT"] = {"qty": 0.1, "order_id": "111",
                                      "stop_id": "222", "tp_id": "333"}
    _stage_round_trip(lp, sym, 1_789_082_000_000 * 1_000_000)
    lp.collect_closed()
    rec = lp.closed_all[-1]
    assert rec["exchange_verified"] is True
    assert rec["exchange_exit_venue"] == "take_profit"
    assert "borsa tarafında" in rec["exchange_note"]
    assert "BTCUSDT" not in lp.mirror_positions
    assert ex.stopped is True and ex.tped is True
