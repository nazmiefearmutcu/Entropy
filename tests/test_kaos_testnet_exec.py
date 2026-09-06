"""Offline unit tests for the KAOS futures-testnet mirroring layer.

No network calls: the HTTP layer is mocked. Covers the signer, symbol/qty
handling, order placement params, balance parsing, env gate, and the
fail-open mirror path used by entropy_live_paper.LivePaper.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kaos_testnet_exec as kx  # noqa: E402
from kaos_testnet_exec import (ExecutorError, TestnetExecutor,  # noqa: E402
                               clean_symbol, make_executor_from_env,
                               mask_key, signed_query)


# ---- signer ----------------------------------------------------------------

def test_signed_query_deterministic_and_complete():
    q1, s1 = signed_query({"symbol": "BTCUSDT"}, "sec", 1_700_000_000_000)
    q2, s2 = signed_query({"symbol": "BTCUSDT"}, "sec", 1_700_000_000_000)
    assert (q1, s1) == (q2, s2)
    assert "symbol=BTCUSDT" in q1
    assert "timestamp=1700000000000" in q1
    assert "recvWindow=10000" in q1
    assert len(s1) == 64 and all(c in "0123456789abcdef" for c in s1)


# ---- helpers ----------------------------------------------------------------

def test_clean_symbol_strips_venue_prefix():
    assert clean_symbol("binance-spot:ETHUSDT") == "ETHUSDT"
    assert clean_symbol("BTCUSDT") == "BTCUSDT"


def test_mask_key_masks_secret_in_text():
    text = "request failed with key AbCdEfGhIjKlMnOp"
    assert "AbCdEfGhIjKlMnOp" not in mask_key(text, "AbCdEfGhIjKlMnOp")
    assert mask_key("clean", "AbCdEfGhIjKlMnOp") == "clean"


# ---- env gate ----------------------------------------------------------------

def test_make_executor_disabled_without_env(monkeypatch):
    for name in ("KAOS_EXCHANGE_TESTNET", "BINANCE_API_KEY", "BINANCE_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert make_executor_from_env() is None


def test_make_executor_needs_all_three(monkeypatch):
    monkeypatch.setenv("KAOS_EXCHANGE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_API_KEY", "k" * 30)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    assert make_executor_from_env() is None


def test_make_executor_enabled(monkeypatch):
    monkeypatch.setenv("KAOS_EXCHANGE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_API_KEY", "k" * 30)
    monkeypatch.setenv("BINANCE_API_SECRET", "s" * 30)
    ex = make_executor_from_env()
    assert isinstance(ex, TestnetExecutor)
    assert ex.host == "https://testnet.binancefuture.com"


# ---- order placement (HTTP mocked) -------------------------------------------

def _executor_with_response(resp) -> tuple[TestnetExecutor, list]:
    ex = TestnetExecutor("key123", "sec123")
    captured: list = []

    def fake_request(method, path, params):
        captured.append((method, path, dict(params)))
        return resp

    ex._request = fake_request  # type: ignore[assignment]
    return ex, captured


def test_place_market_order_params_and_parse():
    ex, cap = _executor_with_response({"orderId": 42, "status": "FILLED",
                                       "avgPrice": "2500.5"})
    out = ex.place_market_order("binance-spot:ETHUSDT", "buy", 0.123456)
    method, path, params = cap[-1]
    assert (method, path) == ("POST", "/fapi/v1/order")
    assert params["symbol"] == "ETHUSDT"
    assert params["side"] == "BUY"
    assert params["type"] == "MARKET"
    assert params["newOrderRespType"] == "RESULT"
    assert "reduceOnly" not in params
    assert out == {"ok": True, "order_id": "42", "status": "FILLED",
                   "avg_price": 2500.5, "qty_used": 0.123, "bumped": False}


def test_place_market_order_reduce_only_side_sell():
    ex, cap = _executor_with_response({"orderId": 7, "status": "FILLED",
                                       "avgPrice": None})
    out = ex.place_market_order("BTCUSDT", "SELL", 1.5, reduce_only=True)
    method, path, params = cap[-1]
    assert params["side"] == "SELL"
    assert params["reduceOnly"] == "true"
    assert out["avg_price"] is None
    assert out["order_id"] == "7"


def test_quantize_uses_lot_step_rounding_down():
    ex = TestnetExecutor("k", "s")
    ex._lot_loaded = True
    ex._lot_step = {"BTCUSDT": 0.001, "DOGEUSDT": 1.0}
    assert ex._quantize("BTCUSDT", 0.123456) == "0.123"
    assert ex._quantize("DOGEUSDT", 999.9) == "999"


def test_quantize_fallback_three_decimals():
    ex = TestnetExecutor("k", "s")
    ex._lot_loaded = True  # bilinmeyen sembol -> 3 dp yedeği
    assert ex._quantize("XYZUSDT", 12.34567) == "12.346"


def test_balance_parses_usdt_row():
    ex, cap = _executor_with_response([
        {"asset": "BNB", "balance": "10", "availableBalance": "10",
         "crossUnPnl": "0"},
        {"asset": "USDT", "balance": "15000.0", "availableBalance": "12000.0",
         "crossUnPnl": "-3.5"},
    ])
    bal = ex.get_balance()
    assert bal == {"wallet": 15000.0, "available": 12000.0, "unrealized": -3.5}
    assert cap[0][1] == "/fapi/v2/balance"


def test_open_positions_filters_zero():
    ex, _ = _executor_with_response([
        {"symbol": "BTCUSDT", "positionAmt": "0.002"},
        {"symbol": "ETHUSDT", "positionAmt": "0"},
        {"symbol": "SOLUSDT", "positionAmt": "-5"},
    ])
    pos = ex.get_open_positions()
    assert pos == [{"symbol": "BTCUSDT", "contracts": 0.002, "side": "long"},
                   {"symbol": "SOLUSDT", "contracts": 5.0, "side": "short"}]


def test_request_masks_key_in_http_error(monkeypatch):
    """HTTPError gövdesindeki hata mesajında anahtar geçerse _request maskelemeli."""
    import io
    import urllib.error

    ex = TestnetExecutor("MySecretKey123456", "sec")
    body = json.dumps({"code": -2008, "msg": "bad key MySecretKey123456"}).encode()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(body))

    monkeypatch.setattr(kx.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ExecutorError) as ei:
        ex.get_balance()
    assert ei.value.code == -2008
    assert "MySecretKey123456" not in ei.value.msg
    assert "****" in ei.value.msg


# ---- fail-open mirror hook (LivePaper._mirror, ağsız stub self) ---------------

def test_mirror_returns_none_when_disabled():
    from entropy_live_paper import LivePaper  # noqa: F401 (import ceiling)
    stub = SimpleNamespace(exec_=None, exec_stats={"orders_sent": 0,
                                                   "orders_failed": 0,
                                                   "last_ok_utc": None,
                                                   "last_error": None},
                           _mirror_live=True)
    out = LivePaper._mirror(stub, "BTCUSDT", "BUY", 0.001)
    assert out is None


def test_mirror_fail_open_on_executor_error():
    from entropy_live_paper import LivePaper

    class Boom:
        def place_market_order(self, *a, **k):
            raise ExecutorError(-2019, "margin insufficient")

    stub = SimpleNamespace(exec_=Boom(),
                           exec_stats={"orders_sent": 0, "orders_failed": 0,
                                       "last_ok_utc": None, "last_error": None},
                           _mirror_live=True)
    out = LivePaper._mirror(stub, "BTCUSDT", "BUY", 0.001)
    assert out is not None and out["ok"] is False
    assert stub.exec_stats["orders_failed"] == 1
    assert stub.exec_stats["orders_sent"] == 0
    assert "margin insufficient" in stub.exec_stats["last_error"]


def test_mirror_success_counts_and_returns():
    from entropy_live_paper import LivePaper

    class Ok:
        def place_market_order(self, *a, **k):
            return {"ok": True, "order_id": "9", "status": "FILLED",
                    "avg_price": 100.0}

    stub = SimpleNamespace(exec_=Ok(),
                           exec_stats={"orders_sent": 0, "orders_failed": 0,
                                       "last_ok_utc": None, "last_error": None},
                           _mirror_live=True)
    out = LivePaper._mirror(stub, "BTCUSDT", "SELL", 0.001, reduce_only=True)
    assert out["ok"] is True
    assert stub.exec_stats["orders_sent"] == 1
    assert stub.exec_stats["last_ok_utc"]


# pytest sınıf-koleksiyonu uyarısını sustur (TestnetExecutor bir test sınıfı değil)
TestnetExecutor.__test__ = False  # type: ignore[attr-defined]


def test_min_notional_bump(monkeypatch):
    """Paper boyutu min notionalin altindaysa emir buyutulur (bumped=True)."""
    ex = TestnetExecutor("key123", "sec123")
    captured = []

    def fake_request(method, path, params):
        captured.append((method, path, dict(params)))
        if path.endswith("exchangeInfo"):
            return {"symbols": [{"symbol": "BTCUSDT", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "50"}]}]}
        if path.endswith("ticker/price"):
            return {"symbol": "BTCUSDT", "price": "50000.0"}
        return {"orderId": 99, "status": "FILLED", "avgPrice": "50000.0"}

    ex._request = fake_request  # type: ignore[assignment]
    # paper notional ~$10 -> testnet min $50 -> ~$50.05'e buyutulmeli
    out = ex.place_market_order("BTCUSDT", "buy", 0.0002)
    method, path, params = captured[-1]
    assert (method, path) == ("POST", "/fapi/v1/order")
    assert float(params["quantity"]) >= 50.0 / 50000.0
    assert out["bumped"] is True
    assert out["order_id"] == "99"

    # reduceOnly cikis buyutulmez (emir boyutu zaten giristen gelir)
    out2 = ex.place_market_order("BTCUSDT", "sell", 0.0002, reduce_only=True)
    assert out2["bumped"] is False


def test_reduce_only_exit_skips_bump(monkeypatch):
    ex = TestnetExecutor("k", "s")
    ex._lot_loaded = True
    paths = []

    def fake_request(method, path, params):
        paths.append(path)
        return {"orderId": 1, "status": "FILLED", "avgPrice": "1.0"}

    ex._request = fake_request  # type: ignore[assignment]
    out = ex.place_market_order("BTCUSDT", "sell", 0.001, reduce_only=True)
    assert out["bumped"] is False
    assert paths == ["/fapi/v1/order"]  # fiyat sorgusu bile atlandi
