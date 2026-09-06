"""Offline unit tests for the KAOS futures-testnet mirroring layer.

No network calls: the HTTP layer is mocked. Covers the signer, symbol/qty
handling, order placement params, balance parsing, env gate, and the
fail-open mirror path used by entropy_live_paper.LivePaper.
"""
from __future__ import annotations

import json
import sys
import time
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

    # F5 Z4-6a: reduceOnly cikista bump yok; $10 < $50 min oldugu icin
    # bu kapanis artik TOZ sayilir ve emir hic acilmaz (DUST_SKIPPED).
    out2 = ex.place_market_order("BTCUSDT", "sell", 0.0002, reduce_only=True)
    assert out2["bumped"] is False
    assert out2["status"] == "DUST_SKIPPED"


def test_reduce_only_exit_skips_bump():
    """F5 Z4-6a notu: reduce-only kapanış artık toz tespiti için fiyat
    sorgular; notional >= min ise (burada 25 x $1 = $25 >= $20) bump
    YOK, emir normal açılır. Toz altı kapanış ayrı testte."""
    ex = TestnetExecutor("k", "s")
    ex._lot_loaded = True
    paths = []

    def fake_request(method, path, params):
        paths.append(path)
        if path.endswith("ticker/price"):
            return {"price": "1.0"}
        return {"orderId": 1, "status": "FILLED", "avgPrice": "1.0"}

    ex._request = fake_request  # type: ignore[assignment]
    out = ex.place_market_order("BTCUSDT", "sell", 25.0, reduce_only=True)
    assert out["bumped"] is False
    assert paths == ["/fapi/v1/ticker/price", "/fapi/v1/order"]


# ---- F5 fix testleri (Z4-4/5/6/3/9/8) — hepsi offline stub --------------------

def test_request_retries_once_on_urlerror(monkeypatch):
    """Z4-5a: URLError -> TAM 1 retry; başarıda sonuç döner."""
    import io
    import urllib.error

    calls, sleeps = [], []
    monkeypatch.setattr(kx.time, "sleep", lambda s: sleeps.append(s))

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.URLError("connection reset")
        return io.BytesIO(json.dumps({"serverTime": 1}).encode())

    monkeypatch.setattr(kx.urllib.request, "urlopen", fake_urlopen)
    ex = TestnetExecutor("k", "s")
    out = ex._request("GET", "/fapi/v1/time", {})
    assert out == {"serverTime": 1}
    assert len(calls) == 2          # ilk deneme + tam 1 retry
    assert len(sleeps) == 1         # retry öncesi tek backoff sleep


def test_request_never_retries_http_error(monkeypatch):
    """Z4-5a: HTTPError (Binance cevap verdi) asla retry edilmez; hata
    mesajındaki genel IP maskelenir ve hata halkasına (Z4-8) yazılır."""
    import io
    import urllib.error

    body = json.dumps({"code": -2019,
                       "msg": "Margin is insufficient. IP(31.223.4.5) x"}).encode()
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(body))

    monkeypatch.setattr(kx.urllib.request, "urlopen", fake_urlopen)
    ex = TestnetExecutor("key123", "sec123")  # kısa anahtar maskeye çakışmasın
    with pytest.raises(ExecutorError) as ei:
        ex.get_balance()
    assert len(calls) == 1                      # retry yok
    assert "31.223.4.5" not in ei.value.msg     # Z4-3: ham IP sızmaz
    assert "IP(<masked>)" in ei.value.msg
    assert len(ex.last_errors) == 1             # Z4-8: hata halkası
    assert "31.223.4.5" not in ex.last_errors[0]


def test_client_order_id_unique_per_order():
    """Z4-5b: ardışık emirlerin newClientOrderId'leri çakışmamalı."""
    ex, cap = _executor_with_response({"orderId": 1, "status": "FILLED",
                                       "avgPrice": "1.0"})
    ex._lot_loaded = True
    ex.place_market_order("BTCUSDT", "buy", 0.5)
    ex.place_market_order("BTCUSDT", "buy", 0.5)
    order_params = [p for (_, path, p) in cap if path == "/fapi/v1/order"]
    assert len(order_params) == 2
    ids = [order_params[0]["newClientOrderId"],
           order_params[1]["newClientOrderId"]]
    assert ids[0].startswith("kaosm")
    assert ids[0] != ids[1]         # aynı ms'de açılan iki emir ayrışır


def test_client_order_id_stable_across_network_retry(monkeypatch):
    """Z4-5b: ağ-retry aynı newClientOrderId'yi taşır (idempotency kalkanı)."""
    import io
    import urllib.error
    import urllib.parse

    order_urls = []

    def fake_urlopen(req, timeout=None):
        u = req.full_url
        if "newClientOrderId" in u:          # yalnız EMİR isteğine ağ hatası
            order_urls.append(u)
            if len(order_urls) == 1:
                raise urllib.error.URLError("reset")
        return io.BytesIO(json.dumps({"orderId": 3, "status": "FILLED",
                                      "avgPrice": "1.0"}).encode())

    monkeypatch.setattr(kx.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(kx.time, "sleep", lambda s: None)
    ex = TestnetExecutor("k", "s")
    ex._lot_loaded = True
    out = ex.place_market_order("BTCUSDT", "buy", 0.5)
    assert out["ok"] is True
    assert len(order_urls) == 2             # 1 başarısız + 1 retry
    qids = [urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            ["newClientOrderId"][0] for u in order_urls]
    assert qids[0] == qids[1]               # retry aynı kimliği yeniden kullanır


def test_minnotional_underscoreless_filter_parsed():
    """Z4-5c: futures'in GERÇEK filtre adı MINNOTIONAL (alt çizgisiz)."""
    ex = TestnetExecutor("k", "s")

    def fake_request(method, path, params):
        assert path.endswith("exchangeInfo")
        return {"symbols": [{"symbol": "XRPUSDT", "filters": [
            {"filterType": "MINNOTIONAL", "notional": "5"},
            {"filterType": "LOT_SIZE", "stepSize": "0.1"}]}]}

    ex._request = fake_request  # type: ignore[assignment]
    ex._load_lot_steps()
    assert ex._lot_loaded is True
    assert ex._min_notional["XRPUSDT"] == 5.0
    assert ex._lot_step["XRPUSDT"] == 0.1


def test_load_lot_steps_bounded_retry():
    """Z4-6b: exchangeInfo hatası fallback'i sonsuza kadar kilitlemez;
    cooldown sonrasında bir sonraki çağrı yeniden dener."""
    ex = TestnetExecutor("k", "s")
    calls = {"n": 0}

    def fake_request(method, path, params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExecutorError(None, "network unreachable")
        return {"symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"}]}]}

    ex._request = fake_request  # type: ignore[assignment]
    ex._load_lot_steps()
    assert ex._lot_loaded is False          # hata fallback'i KİLİTLEMEZ
    assert calls["n"] == 1
    ex._load_lot_steps()                    # cooldown içinde: yeniden denemez
    assert calls["n"] == 1
    ex._lot_fail_at -= kx._LOT_RETRY_COOLDOWN_S + 1.0   # cooldown geçmiş olsun
    ex._load_lot_steps()
    assert ex._lot_loaded is True
    assert ex._lot_step["BTCUSDT"] == 0.001
    assert calls["n"] == 2


@pytest.mark.parametrize("step", ["0.001", "0.01", "0.1", "1"])
@pytest.mark.parametrize("price", ["9.999", "123.45", "0.5", "3.21"])
def test_bump_quantize_roundtrip_stays_above_floor(step, price):
    """Z4-4: bump sonrası _quantize bump'ı geri ALAMAMALI. Invariant:
    FINAL quantized notional >= min_notional VE qty adım katı."""
    ex = TestnetExecutor("k", "s")
    cap = []

    def fake_request(method, path, params):
        cap.append(path)
        if path.endswith("exchangeInfo"):
            return {"symbols": [{"symbol": "AAAUSDT", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": step},
                {"filterType": "MINNOTIONAL", "notional": "20"}]}]}
        if path.endswith("ticker/price"):
            return {"price": price}
        return {"orderId": 5, "status": "FILLED", "avgPrice": price}

    ex._request = fake_request  # type: ignore[assignment]
    p, st = float(price), float(step)
    paper_qty = (20.0 / p) * 0.5            # paper notional tabanın yarısı
    out = ex.place_market_order("AAAUSDT", "buy", paper_qty)
    q = float(out["qty_used"])
    assert out["bumped"] is True
    assert q * p >= 20.0 - 1e-9             # Z4-4 invariant: quantize SONRASI taban üstü
    n = q / st
    assert abs(n - round(n)) < 1e-6         # adım katı geçerliliği korunur


def test_bump_truncation_class_review_example():
    """Z4-4'ün birebir kanıt senaryosu: fiyat 9.999, step 0.001, min $20.
    Eski kod: bump 2.001 -> _quantize 2.000 -> notional 19.998 -> -4164."""
    ex = TestnetExecutor("k", "s")

    def fake_request(method, path, params):
        if path.endswith("exchangeInfo"):
            return {"symbols": [{"symbol": "BBBUSDT", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "MINNOTIONAL", "notional": "20"}]}]}
        if path.endswith("ticker/price"):
            return {"price": "9.999"}
        return {"orderId": 7, "status": "FILLED", "avgPrice": "9.999"}

    ex._request = fake_request  # type: ignore[assignment]
    out = ex.place_market_order("BBBUSDT", "buy", 2.0)
    q = float(out["qty_used"])
    assert q * 9.999 >= 20.0                 # eski kod burada 19.998 ile patlardı
    assert out["bumped"] is True


def test_dust_close_skips_order():
    """Z4-6a: <$min toz reduce-only kapanış emri HİÇ açılmaz; DUST_SKIPPED
    marker'ı döner (retry gürültüsü biter). Toz üstü kapanış normal akar."""
    ex = TestnetExecutor("k", "s")
    paths = []

    def fake_request(method, path, params):
        paths.append(path)
        if path.endswith("ticker/price"):
            return {"price": "0.5"}
        return {"orderId": 9, "status": "FILLED", "avgPrice": "0.5"}

    ex._request = fake_request  # type: ignore[assignment]
    ex._lot_loaded = True
    ex._lot_step = {"XRPUSDT": 0.1}
    out = ex.place_market_order("XRPUSDT", "SELL", 1.0, reduce_only=True)
    assert out["ok"] is True and out["status"] == "DUST_SKIPPED"
    assert out["order_id"] is None and out.get("skipped") == "dust"
    assert paths == ["/fapi/v1/ticker/price"]      # emir endpoint'ine gidilmedi

    out2 = ex.place_market_order("XRPUSDT", "SELL", 100.0, reduce_only=True)
    assert out2["ok"] is True and out2["order_id"] == "9"   # $50 >= $20: normal
    assert paths[-1] == "/fapi/v1/order"


def test_ban_1003_parsed_and_requests_paused(monkeypatch):
    """Z4-3: -1003 'banned until <epoch>' ayrıştırılır; ban aktifken istek
    AĞA ÇIKMADAN MirrorBanned ile reddedilir; ham IP mesaja sızmaz."""
    import io
    import urllib.error

    until_ms = int(time.time() * 1000) + 3_600_000
    body = json.dumps({"code": -1003,
                       "msg": f"Way too many requests; IP(31.223.4.5) "
                              f"banned until {until_ms}"}).encode()
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                     {}, io.BytesIO(body))

    monkeypatch.setattr(kx.urllib.request, "urlopen", fake_urlopen)
    ex = TestnetExecutor("k", "s")
    with pytest.raises(kx.MirrorBanned) as ei:
        ex.get_balance()
    assert "31.223.4.5" not in str(ei.value)     # maskeleme
    assert ex.banned_until_ms() == until_ms      # kesin vade parse edildi
    assert ex.is_banned() is True
    n = len(calls)
    with pytest.raises(kx.MirrorBanned):
        ex.get_balance()
    assert len(calls) == n                       # ban altında ağa çıkılmadı
    assert ex.last_errors and "banned until" in ex.last_errors[-1]

    # vade geçince istekler kendiliğinden döner (self-expire)
    ex._banned_until = time.time() - 1.0
    assert ex.is_banned() is False


def test_ban_backoff_exponential_when_until_unparsable(monkeypatch):
    """Z4-3: mesajda kesin vade yoksa üstel backoff (60 -> 120 ... -> 900 sn)."""
    import io
    import urllib.error

    body = json.dumps({"code": -1003, "msg": "Way too many requests"}).encode()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                     {}, io.BytesIO(body))

    monkeypatch.setattr(kx.urllib.request, "urlopen", fake_urlopen)
    ex = TestnetExecutor("k", "s")
    with pytest.raises(kx.MirrorBanned):
        ex.get_balance()
    w1 = ex.banned_until_ms() / 1000.0 - time.time()
    assert kx._BAN_BACKOFF_START_S - 5 <= w1 <= kx._BAN_BACKOFF_START_S + 5

    ex._banned_until = 0.0                        # vadenin dolduğunu simüle et
    with pytest.raises(kx.MirrorBanned):
        ex.get_balance()
    w2 = ex.banned_until_ms() / 1000.0 - time.time()
    assert w2 >= 2 * kx._BAN_BACKOFF_START_S - 5  # backoff ikiye katlandı


def test_clock_drift_1021_resync_and_retry(monkeypatch):
    """Z4-9: -1021'de sunucu zamanı ofseti ölçülür, istek düzeltilmiş
    timestamp ile BİR KEZ yeniden denenir; ikinci -1021 tekrar senkronlamaz."""
    import io
    import urllib.error

    calls = []
    server_now = time.time() * 1000.0 + 5000.0

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            body = json.dumps({"code": -1021,
                               "msg": "Timestamp for this request is outside "
                                      "of the recvWindow."}).encode()
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request",
                                         {}, io.BytesIO(body))
        if "/fapi/v1/time" in req.full_url:
            return io.BytesIO(json.dumps(
                {"serverTime": int(server_now)}).encode())
        return io.BytesIO(json.dumps([{"asset": "USDT", "balance": "1",
                                       "availableBalance": "1",
                                       "crossUnPnl": "0"}]).encode())

    monkeypatch.setattr(kx.urllib.request, "urlopen", fake_urlopen)
    ex = TestnetExecutor("k", "s")
    out = ex.get_balance()
    assert out["wallet"] == 1.0                            # retry başarılı
    assert 4300.0 <= ex._time_offset_ms <= 5700.0          # ofset ölçüldü
    assert len(calls) == 3                       # -1021 -> time senkronu -> retry

    # resync edilmiş ikinci -1021: yeni senkron yok, protokol hatası yükselir
    calls.clear()

    def fake_urlopen_1021(req, timeout=None):
        calls.append(req.full_url)
        body = json.dumps({"code": -1021, "msg": "Timestamp ..."}).encode()
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request",
                                     {}, io.BytesIO(body))

    monkeypatch.setattr(kx.urllib.request, "urlopen", fake_urlopen_1021)
    with pytest.raises(ExecutorError) as ei:
        ex._request("GET", "/fapi/v2/balance", {}, _resynced=True)
    assert ei.value.code == -1021
    assert len(calls) == 1     # _resynced guard: senkron + retry TEKRARLANMAZ
