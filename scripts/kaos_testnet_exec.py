#!/usr/bin/env python3
"""kaos_testnet_exec.py — KAOS canlı runner için Binance FUTURES TESTNET
aynalama katmanı (yalnız stdlib; ccxt bağımlılığı EKLENMEZ).

AMAÇ (spec: C:/botmonitor/docs/superpowers/specs/2026-09-06-baglanti-dogrulama-design.md §3 T3):
Runner'ın her canlı paper dolgusu, testnet'te GERÇEK market emri olarak
tekrarlanır; işlem kayıtları borsa emir kimlikleriyle işaretlenir → kâr,
borsadan geri okunarak doğrulanabilir olur. Kural DAİME FAIL-OPEN: bu
katmandaki hiçbir hata runner'ı düşürmez, paper muhasebeyi bozmaz.

Güvenlik: anahtar yalnız env'den okunur (KAOS_EXCHANGE_TESTNET=1 +
BINANCE_API_KEY + BINANCE_API_SECRET); hiçbir loga/dosyaya yazılmaz.
Yalnız futures testnet hostuna bağlanır (mainnet emir yolu KASITLI YOKTUR).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HOST_DEFAULT = "https://testnet.binancefuture.com"
_TIMEOUT_S = 8
_RECV_WINDOW = 10000
_KEY_MASK = "****"
# futures exchangeInfo filtresi "MINNOTIONAL" (spot'taki gibi "MIN_NOTIONAL"
# DEĞİL) — iki ad da okunur; filtre hiç gelirse emin taban (testnet sert
# sınırı, -4164) devreye girer:
_MIN_NOTIONAL_FLOOR = 20.0


class ExecutorError(Exception):
    """Binance hata gövdesi (code, msg) — mesaj anahtar-sızdırılmış."""

    def __init__(self, code, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg or ""


def mask_key(text: str, api_key: str) -> str:
    if api_key and api_key in text:
        return text.replace(api_key, _KEY_MASK)
    return text


def clean_symbol(symbol: str) -> str:
    """'binance-spot:ETHUSDT' / 'binance-futures:ETHUSDT' -> 'ETHUSDT'."""
    return str(symbol).split(":", 1)[-1]


def signed_query(params: dict, api_secret: str, ts_ms: int) -> tuple[str, str]:
    """(query, signature) — imzacının saf, test edilebilir kalbi."""
    p = dict(params)
    p["timestamp"] = ts_ms
    p["recvWindow"] = _RECV_WINDOW
    query = urllib.parse.urlencode(p)
    sig = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return query, sig


class TestnetExecutor:
    """Binance USDT-M futures TESTNET istemcisi (imzalı, market emir)."""

    def __init__(self, api_key: str, api_secret: str,
                 host: str = HOST_DEFAULT) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.host = host.rstrip("/")
        self._lot_step: dict[str, float] = {}   # symbol -> LOT_SIZE stepSize
        self._min_notional: dict[str, float] = {}  # symbol -> min notional USDT
        self._lot_loaded = False

    # ---- low level ------------------------------------------------------
    def _request(self, method: str, path: str, params: dict,
                 tries: int = 2) -> Any:
        """İmzalı istek. Ağ kopukluklarında (URLError/timeout) 1 kez yeniden
        dener — 2026-09-06: testnet hostuna aralıklı kopukluklar mirror
        emirlerini sessizce düşürüyor, paper-borsa uyumsuzluğu yaratıyordu.
        HTTPError (Binance cevap verdi) retry EDİLMEZ: protokol hatasıdır."""
        last_exc: Exception | None = None
        for attempt in range(max(1, tries)):
            query, sig = signed_query(params, self.api_secret,
                                      int(time.time() * 1000))
            url = f"{self.host}{path}?{query}&signature={sig}"
            req = urllib.request.Request(url, headers={
                "X-MBX-APIKEY": self.api_key,
                "User-Agent": "kaos-live-paper/1.0",
            }, method=method)
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = {}
                msg = mask_key(str(payload.get("msg") or body[:200]),
                               self.api_key)
                raise ExecutorError(payload.get("code"), msg) from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt + 1 < tries:
                    time.sleep(1.5)
        raise ExecutorError(
            None, f"network unreachable: {type(last_exc).__name__}"
        ) from last_exc

    # ---- public API -----------------------------------------------------
    def get_balance(self) -> dict:
        """USDT satırı: {wallet, available, unrealized} (float)."""
        rows = self._request("GET", "/fapi/v2/balance", {})
        out = {"wallet": 0.0, "available": 0.0, "unrealized": 0.0}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get("asset") != "USDT":
                continue
            out = {
                "wallet": float(row.get("balance") or 0.0),
                "available": float(row.get("availableBalance") or 0.0),
                "unrealized": float(row.get("crossUnPnl") or 0.0),
            }
            break
        return out

    def _load_lot_steps(self) -> None:
        if self._lot_loaded:
            return
        self._lot_loaded = True  # başarısızlıkta tekrar deneme spam'ini önle
        try:
            info = self._request("GET", "/fapi/v1/exchangeInfo", {})
            for s in info.get("symbols") or []:
                if not isinstance(s, dict):
                    continue
                for f in s.get("filters") or []:
                    if not isinstance(f, dict):
                        continue
                    ftype = f.get("filterType")
                    if ftype == "LOT_SIZE":
                        step = float(f.get("stepSize") or 0.0)
                        if step > 0:
                            self._lot_step[str(s.get("symbol"))] = step
                    elif ftype in ("MIN_NOTIONAL", "MINNOTIONAL"):
                        self._min_notional[str(s.get("symbol"))] = float(
                            f.get("notional") or 0.0)
        except Exception:
            pass  # fail-open: bilinmeyen adım -> 3 dp yuvarlama yedeği

    def get_price(self, symbol: str) -> float:
        """Son fiyat (testnet ticker) — notional yükseltmesi için."""
        sym = clean_symbol(symbol)
        row = self._request("GET", "/fapi/v1/ticker/price",
                            {"symbol": sym})
        return float(row.get("price") or 0.0)

    def _quantize(self, symbol: str, qty: float) -> str:
        self._load_lot_steps()
        step = self._lot_step.get(symbol)
        if step and step > 0:
            q = (int(qty / step)) * step
            decimals = max(0, len(f"{step:.10f}".rstrip("0").split(".")[1]))
            return f"{q:.{decimals}f}"
        return f"{qty:.3f}"

    def place_market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> dict:
        """MARKET emir → {ok, order_id, status, avg_price, qty_used, bumped}.
        Paper boyutu testnet minimum notionalının (20-50 USDT) altındaysa
        emir BÜYÜTÜLÜR ve 'bumped' bayrağıyla bildirilir — aynalama sapması
        işlem kaydına dürüstçe yazılır; sessiz yutulmaz."""
        sym = clean_symbol(symbol)
        used_qty, bumped = qty, False
        try:
            self._load_lot_steps()
            # filtre okunamazsa bile testnet sert sınırının (-4164) altına
            # inme: emin taban her zaman devrede
            min_notional = max(self._min_notional.get(sym, 0.0),
                               _MIN_NOTIONAL_FLOOR)
            if not reduce_only:
                price = self.get_price(sym)
                if price > 0 and used_qty * price < min_notional:
                    raw = min_notional / price
                    # bump'ta YUKARI yuvarla: normal quantize'ın aşağı
                    # yuvarlaması notional'ı tekrar sınırın altına
                    # düşürebilir (-4164; 2026-09-06 birim testte yakalandı)
                    step = self._lot_step.get(sym, 0.0)
                    used_qty = ((int(raw / step) + 1) * step) if step > 0 \
                        else raw * 1.001
                    bumped = True
        except Exception:
            pass  # fiyata erişilemezse paper qty ile dene (borsa reddederse
                  # MirrorResult ok=False olur — fail-open sözleşmesi)
        params: dict[str, Any] = {
            "symbol": sym,
            "side": "BUY" if side.upper().startswith("B") else "SELL",
            "type": "MARKET",
            "quantity": self._quantize(sym, used_qty),
            "newOrderRespType": "RESULT",
            # çift-emir koruması: ağ-retry aynı emri yeniden gönderirse
            # Binance aynı clientOrderId'yi reddeder (idempotency kalkanı)
            "newClientOrderId": f"kaosm{int(time.time() * 1000)}",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        resp = self._request("POST", "/fapi/v1/order", params)
        avg = resp.get("avgPrice")
        return {
            "ok": True,
            "order_id": str(resp.get("orderId") or ""),
            "status": str(resp.get("status") or "?"),
            "avg_price": (float(avg) if avg not in (None, "", 0, "0") else None),
            "qty_used": float(params["quantity"]),
            "bumped": bumped,
        }

    def get_open_positions(self) -> list[dict]:
        """Sıfırdan farklı açık pozisyonlar: [{symbol, contracts, side}]."""
        rows = self._request("GET", "/fapi/v2/positionRisk", {})
        out = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            amt = float(row.get("positionAmt") or 0.0)
            if amt == 0.0:
                continue
            out.append({
                "symbol": str(row.get("symbol")),
                "contracts": abs(amt),
                "side": "long" if amt > 0 else "short",
            })
        return out


def make_executor_from_env() -> TestnetExecutor | None:
    """Env ayarlarına göre executor üretir; aynalama kapalıysa None.
    Kapalıyken runner davranışı bit-özdeş kalır (hiçbir import yan etkisi yok)."""
    enabled = str(os.environ.get("KAOS_EXCHANGE_TESTNET", "")).strip().lower() in ("1", "true")
    key = os.environ.get("BINANCE_API_KEY", "").strip()
    secret = os.environ.get("BINANCE_API_SECRET", "").strip()
    if not (enabled and key and secret):
        return None
    print("[kaos-exec] testnet mirror ENABLED (futures testnet, "
          "yalnız testnet hostuna emir)", flush=True)
    return TestnetExecutor(key, secret)
