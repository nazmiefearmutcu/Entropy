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

2026-09-06 F5 fixleri (review: C:/botmonitor/reports/review-2026-09-06/Z4-kaos-mirror.md):
- [Z4-4] entry bump → _quantize taban yuvarlaması bump'ı geri alıyordu
  (fiyat 9.999/step 0.001 → 19.998 USDT → -4164). Artık bump hedefi küçük
  headroom'lu tabandır ve FINAL quantize edilmiş notional invariant olarak
  tabanın üstünde garanti edilir (adım katı geçerliliği korunur).
- [Z4-6a] sub-notional (toz) reduce-only kapanışlar -4164 ile sürekli
  reddediliyordu (XRP $0.14 kalıcı toz + 900 sn'de bir retry gürültüsü).
  Toz kapanışları emir HİÇ açılmadan DUST_SKIPPED marker ile atlanır.
- [Z4-6b] exchangeInfo bir kez başarısız olursa 3dp fallback sonsuza kadar
  kilitleniyordu; artık bounded retry (300 sn cooldown, fallback ara sıra).
- [Z4-3] -1003/429/418 IP banı: "banned until" ayrıştırılır, ban bitene
  kadar _request kapıda MirrorBanned ile reddeder (runner fail-open tolere
  eder), kesin vade yoksa üstel backoff (60→900 sn). Accessor'lar:
  banned_until_ms()/is_banned() — runner (F4) feature-detect ile okur.
  -1003 mesajındaki genel IP ham hâlde ASLA dışarı sızmaz (IP(<masked>)).
- [Z4-9] -1021 saat kayması: sunucu zamanı ofseti bir kez ölçülür ve istek
  düzeltilmiş timestamp ile bir kez yeniden denenir.
- [Z4-8] executor tarafında son-5 hata halkası (last_errors; state yazımı
  runner'da kalır — F4 recent_errors ile orayı halletti).
- [Z4-10] _TIMEOUT_S 8→5 sn: tek mirror emrinin döngüyü bloklaması en kötü
  5*2+1.5 ≈ 11.5 sn'ye iner (önceki ~17.5 sn).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any

HOST_DEFAULT = "https://testnet.binancefuture.com"
HOST_MAINNET = "https://fapi.binance.com"  # GERCEK HESAP (kullanici karari 09-07)
_TIMEOUT_S = 5          # Z4-10: 8 -> 5 (tek emir döngü bloğu ~11.5 sn'a iner)
_RECV_WINDOW = 10000
_KEY_MASK = "****"
# futures exchangeInfo filtresi "MINNOTIONAL" (spot'taki gibi "MIN_NOTIONAL"
# DEĞİL) — iki ad da okunur; filtre hiç gelirse emin taban (testnet sert
# sınırı, -4164) devreye girer:
_MIN_NOTIONAL_FLOOR = 20.0
_BUMP_HEADROOM = 1.001          # Z4-4: bump hedefine %0.1 MARKET-kayma payı
_BAN_BACKOFF_START_S = 60.0     # Z4-3: kesin "banned until" yoksa backoff
_BAN_BACKOFF_MAX_S = 900.0
_LOT_RETRY_COOLDOWN_S = 300.0   # Z4-6b: exchangeInfo hata sonrası retry aralığı

_BAN_UNTIL_RE = re.compile(r"banned until (\d{10,16})")
_IP_RE = re.compile(r"IP\s*\(?\s*\d{1,3}(?:\.\d{1,3}){3}\s*\)?")


class ExecutorError(Exception):
    """Binance hata gövdesi (code, msg) — mesaj anahtar-sızdırılmış."""

    def __init__(self, code, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg or ""


class MirrorBanned(ExecutorError):
    """-1003/429/418 IP banı aktifken istek reddi (Z4-3). Runner'ın fail-open
    Exception avcıları bunu normal hata gibi tolere eder; ban bitince istekler
    kendiliğinden döner. Mesaj ham IP/anahtar İÇERMEZ."""


def mask_key(text: str, api_key: str) -> str:
    if api_key and api_key in text:
        return text.replace(api_key, _KEY_MASK)
    return text


def _mask_ip(text: str) -> str:
    """Z4-3: -1003 ban mesajı makinenin GENEL IP'sini taşır — bu modülden
    çıkan hiçbir hata metnine ham IP yazılmaz (runner tarafındaki _mask_ip
    ile aynı semantik; iki taraf da aynı maske formatını üretir)."""
    return _IP_RE.sub("IP(<masked>)", str(text))


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
                 host: str = HOST_DEFAULT, leverage: int = 1,
                 max_notional_usdt: float = 0.0) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.host = host.rstrip("/")
        # GERCEK HESAP korumalari (kullanici karari 09-07): kaldıraç 1..20
        # (varsayılan 1 = kapalı), pozisyon başına notional tavanı (0 = kapalı)
        self.leverage = max(1, min(20, int(leverage)))
        self.max_notional_usdt = float(max_notional_usdt or 0.0)
        self._lev_done: set[str] = set()      # kaldıraç/izole ayarlanan semboller
        self._stop_orders: dict[str, str] = {}  # symbol -> STOP_MARKET orderId
        self._lot_step: dict[str, float] = {}   # symbol -> LOT_SIZE stepSize
        self._tick_size: dict[str, float] = {}  # symbol -> PRICE_FILTER tickSize
        self._min_notional: dict[str, float] = {}  # symbol -> min notional USDT
        self._lot_loaded = False
        self._lot_fail_at = 0.0        # Z4-6b: son exchangeInfo hatası (monotonic benzeri epoch)
        self._banned_until = 0.0       # Z4-3: ban bitişi (epoch saniye; 0 = ban yok)
        self._ban_backoff_s = _BAN_BACKOFF_START_S
        self._time_offset_ms = 0.0     # Z4-9: sunucu - yerel saat ofseti (ms)
        self._oid_seq = 0              # Z4-5: newClientOrderId ms-içi çakışma önleyici
        self.last_errors: deque[str] = deque(maxlen=5)  # Z4-8: son-5 hata halkası

    # ---- clock + ban state ----------------------------------------------

    def _now_ms(self) -> int:
        """İmza timestamp'i: yerel saat + ölçülmüş sunucu ofseti (Z4-9)."""
        return int(time.time() * 1000.0 + self._time_offset_ms)

    def is_banned(self, now_ms: float | None = None) -> bool:
        """-1003 IP banı aktif mi? (Z4-3)"""
        now = float(now_ms) if now_ms is not None else time.time() * 1000.0
        return bool(self._banned_until and now < self._banned_until * 1000.0)

    def banned_until_ms(self) -> int:
        """Aktif banın bitişi (epoch ms) veya 0. Runner (entropy_live_paper)
        bu accessor'ı feature-detect ile okur — isim/imza sabit kalmalı."""
        return int(self._banned_until * 1000.0) if self._banned_until else 0

    def _ban_iso(self) -> str:
        if not self._banned_until:
            return "unknown"
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._banned_until))

    def _register_ban(self, raw_msg: str) -> None:
        """-1003/429/418 cevabında ban durumunu güncelle (Z4-3). Mesajda
        'banned until <epoch>' varsa kesin vade kullanılır; yoksa üstel
        backoff (60 -> 120 -> ... -> 900 sn) ile geçici vade konur."""
        now = time.time()
        until = 0.0
        m = _BAN_UNTIL_RE.search(raw_msg or "")
        if m:
            v = int(m.group(1))
            until = (v / 1000.0) if v >= 10**12 else float(v)  # ms/sn normalizasyonu
        if until > now:
            self._ban_backoff_s = _BAN_BACKOFF_START_S  # kesin vade: backoff reset
        else:
            until = now + self._ban_backoff_s
            self._ban_backoff_s = min(self._ban_backoff_s * 2.0, _BAN_BACKOFF_MAX_S)
        self._banned_until = max(self._banned_until, until)

    def _remember_error(self, msg: str) -> None:
        """Z4-8: hata halkası (son 5). Maskesiz metin girmesin."""
        self.last_errors.append(_mask_ip(msg)[:200])

    def _sync_server_time(self) -> bool:
        """Z4-9: /fapi/v1/time ile sunucu-ofsetini ölç. Başarısızlıkta sessiz
        (fail-open) — mevcut ofsetle devam."""
        try:
            raw_local = time.time() * 1000.0
            rows = self._request("GET", "/fapi/v1/time", {}, tries=1,
                                 _resynced=True)
            server = float(rows.get("serverTime") or 0.0)
            if server > 0:
                self._time_offset_ms = server - raw_local
                return True
        except Exception:  # noqa: BLE001 — senkron başarısız = mevcut davranış
            pass
        return False

    # ---- low level ------------------------------------------------------
    def _request(self, method: str, path: str, params: dict,
                 tries: int = 2, _resynced: bool = False) -> Any:
        """İmzalı istek.
        - Ağ kopukluğu (URLError/timeout): 1 kez retry; HTTPError (Binance
          cevap verdi) retry EDİLMEZ: protokol hatasıdır.
        - Z4-3: ban aktifken istek hiç ağa çıkmaz, MirrorBanned fırlatır.
          -1003/429/418 cevabı ban kaydeder (kesin vade parse; yoksa üstel
          backoff). Hata metinleri IP/anahtar maskesiz çıkmaz.
        - Z4-9: -1021'de sunucu zamanı ile bir kez senkronlanıp istek bir
          kez düzeltilmiş timestamp ile yeniden denenir (_resynced guard'ı
          sonsuz döngüyü keser)."""
        if self.is_banned():
            raise MirrorBanned(
                -1003, f"mirror paused until {self._ban_iso()} (IP ban backoff)"
            ) from None
        last_exc: Exception | None = None
        for attempt in range(max(1, tries)):
            query, sig = signed_query(params, self.api_secret, self._now_ms())
            url = f"{self.host}{path}?{query}&signature={sig}"
            req = urllib.request.Request(url, headers={
                "X-MBX-APIKEY": self.api_key,
                "User-Agent": "kaos-live-paper/1.0",
            }, method=method)
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                    # başarılı istek: backoff tabana dönsün (ban yok/temiz)
                    self._ban_backoff_s = _BAN_BACKOFF_START_S
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = {}
                raw_msg = str(payload.get("msg") or body[:200])
                msg = mask_key(_mask_ip(raw_msg), self.api_key)
                code = payload.get("code")
                # Z4-9: saat kayması — bir kez senkronla, bir kez retry et
                if code == -1021 and not _resynced:
                    if self._sync_server_time():
                        return self._request(method, path, params,
                                             tries=tries, _resynced=True)
                    raise ExecutorError(code, msg) from None
                # Z4-3: IP banı — kaydet ve kapıda reddet
                if code == -1003 or getattr(exc, "code", None) in (418, 429):
                    self._register_ban(raw_msg)
                    banner = (f"IP banned until {self._ban_iso()} "
                              f"(mirror paused; detail masked)")
                    self._remember_error(f"{code}: {banner}")
                    raise MirrorBanned(
                        code if code is not None else -1003, banner) from None
                self._remember_error(f"{code}: {msg}")
                raise ExecutorError(code, msg) from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt + 1 < tries:
                    time.sleep(1.5)
        err = f"network unreachable: {type(last_exc).__name__}"
        self._remember_error(err)
        raise ExecutorError(None, err) from last_exc

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
        """exchangeInfo -> stepSize + minNotional haritaları.
        Z4-6b: yalnız BAŞARIDA _lot_loaded set edilir; hatada 300 sn
        cooldown ile bir sonraki emirde yeniden denenir (eski kod tek
        hatayı süreç ömrü boyunca 3dp fallback'e kilitliyordu)."""
        if self._lot_loaded:
            return
        now = time.time()
        if self._lot_fail_at and (now - self._lot_fail_at) < _LOT_RETRY_COOLDOWN_S:
            return  # cooldown: fallback 3dp ile devam (spam yok, bounded retry)
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
                    elif ftype == "PRICE_FILTER":
                        tick = float(f.get("tickSize") or 0.0)
                        if tick > 0:
                            self._tick_size[str(s.get("symbol"))] = tick
                    elif ftype in ("MIN_NOTIONAL", "MINNOTIONAL"):
                        self._min_notional[str(s.get("symbol"))] = float(
                            f.get("notional") or 0.0)
            self._lot_loaded = True  # Z4-6b: yalnız başarıda kilitle
        except Exception:  # noqa: BLE001
            self._lot_fail_at = now  # bounded retry: cooldown sonrası tekrar dene

    def get_price(self, symbol: str) -> float:
        """Son fiyat (testnet ticker) — notional yükseltmesi için."""
        sym = clean_symbol(symbol)
        row = self._request("GET", "/fapi/v1/ticker/price",
                            {"symbol": sym})
        return float(row.get("price") or 0.0)

    @staticmethod
    def _step_count(qty: float, step: float, direction: str) -> int:
        """qty'nin step kat sayısı. Float artefaktına dayanıklı: oran bir
        kat sayısına yakınsa (ör. 2000.9999999999998) TAM KAT sayılır — eski
        int(qty/step) taban yuvarlaması tam bu artefaktla bump'ı siliyordu
        (Z4-4). direction='up' en küçük üstün katı verir."""
        ratio = qty / step
        nearest = round(ratio)
        if nearest > 0 and abs(ratio - nearest) <= 1e-6 * max(1.0, abs(nearest)):
            return int(nearest)
        if direction == "up":
            return int(math.ceil(ratio))
        return int(math.floor(ratio))

    def _quantize(self, symbol: str, qty: float, direction: str = "down") -> str:
        """qty -> stepSize'a oturtulmuş string. direction='up' (yalnız bump'lı
        ENTRY'lerde) taban yuvarlamasının bump'ı geri almasını engeller."""
        self._load_lot_steps()
        step = self._lot_step.get(symbol)
        if step and step > 0:
            q = self._step_count(qty, step, direction) * step
            decimals = max(0, len(f"{step:.10f}".rstrip("0").split(".")[1]))
            return f"{q:.{decimals}f}"
        # fallback 3dp (step haritası yok): 'up' tavan 3dp, aksi taban
        if direction == "up" and qty > 0:
            return f"{math.ceil(qty * 1000.0 - 1e-6) / 1000.0:.3f}"
        return f"{qty:.3f}"

    # ---- GERCEK HESAP korumalari (kullanici karari 09-07) ----------------

    def _ensure_lev_isolated(self, sym: str) -> None:
        """Sembole KALDIRAC + IZOLE marj ayarla (semhol basina bir kez).
        -4046 ('zaten bu modda') tolere edilir; DİĞER hata raise eder —
        yanlış marj modunda (CROSS) gerçek pozisyon AÇILMAZ."""
        if self.leverage <= 1 or sym in self._lev_done:
            return
        try:
            self._request("POST", "/fapi/v1/marginType",
                          {"symbol": sym, "marginType": "ISOLATED"})
        except Exception as exc:
            msg = str(exc)
            if "-4046" not in msg and "No need" not in msg:
                self._remember_error(f"marginType FAILED {sym}: "
                                     f"{_mask_ip(msg)[:120]}")
                raise
        self._request("POST", "/fapi/v1/leverage",
                      {"symbol": sym, "leverage": self.leverage})
        self._lev_done.add(sym)

    def _round_stop(self, sym: str, pos_side: str, price: float) -> float:
        """SL tetik fiyatini tick gridine GUVENLI yuvarla: long SL ASAGI,
        short SL YUKARI (tetikleme kural fiyatindan gec olmasin)."""
        tick = self._tick_size.get(sym, 0.0)
        price = float(price)
        if not tick:
            return price
        n = price / tick
        steps = math.floor(n + 1e-9) if pos_side == "long" else math.ceil(n - 1e-9)
        return round(steps * tick, 12)

    def place_venue_stop(self, sym: str, pos_side: str, stop_price: float) -> str | None:
        """Borsa tarafında koruyucu STOP_MARKET (closePosition=true, MARK_PRICE).
        Süreç ölsem bile pozisyon borsada korunur. Best-effort: giriş doldu,
        yerleşemezsesi LOUD log + None (pozisyon orphan olmaz, bot içi SL
        da çalışmaya devam eder)."""
        if not stop_price or float(stop_price) <= 0:
            return None
        trigger = self._round_stop(sym, pos_side, float(stop_price))
        params = {
            "symbol": sym,
            "side": "SELL" if pos_side == "long" else "BUY",
            "type": "STOP_MARKET",
            "stopPrice": trigger,
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "newOrderRespType": "RESULT",
            "newClientOrderId": f"kaoss{int(time.time() * 1000)}"
                                f"{self._oid_seq % 10000:04d}",
        }
        try:
            resp = self._request("POST", "/fapi/v1/order", params)
            oid = str(resp.get("orderId") or "")
            if oid:
                self._stop_orders[sym] = oid
            print(f"[kaos-exec] venue stop yerlesti: {sym} {pos_side} "
                  f"trigger={trigger} id={oid}", flush=True)
            return oid or None
        except Exception as exc:
            msg = _mask_ip(str(exc))[:160]
            self._remember_error(f"venue stop FAILED {sym}: {msg}")
            print(f"[kaos-exec] venue stop YERLESTIRILEMEDI {sym}: {msg}",
                  flush=True)
            return None

    def cancel_venue_stop(self, sym: str) -> bool:
        """İzlenen STOP_MARKET'i iptal et (-2011 'zaten yok' tolere)."""
        oid = self._stop_orders.pop(sym, None)
        if not oid:
            return False
        try:
            self._request("DELETE", "/fapi/v1/order",
                          {"symbol": sym, "orderId": oid})
            return True
        except Exception as exc:
            msg = str(exc)
            if "-2011" not in msg and "Unknown order" not in msg:
                self._remember_error(f"venue stop cancel FAILED {sym}: "
                                     f"{_mask_ip(msg)[:120]}")
            return False

    def cancel_stale_venue_stops(self, symbols=None) -> int:
        """Başlangıç temizliği: izlenen sembollerdeki STOP_MARKET emirlerini
        iptal et (pozisyon yoksa). Geriye kalan sayı."""
        syms = list(symbols) if symbols else list(self._stop_orders)
        n = 0
        for sym in syms:
            if self.cancel_venue_stop(sym):
                n += 1
        return n

    def place_market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> dict:
        """MARKET emir → {ok, order_id, status, avg_price, qty_used, bumped}.
        Paper boyutu testnet minimum notionalının (20-50 USDT) altındaysa
        ENTRY emri BÜYÜTÜLÜR ve 'bumped' bayrağıyla bildirilir — aynalama
        sapması işlem kaydına dürüstçe yazılır; sessiz yutulmaz.
        Z4-6a: reduce-only kapanış notionalı min altındaysa (toz) emir
        HİÇ açılmaz — DUST_SKIPPED döner (retry gürültüsü biter)."""
        sym = clean_symbol(symbol)
        used_qty, bumped = qty, False
        if reduce_only:
            # kapanista borsa-tarafı stopu ONCE çek (dust-skip olsa bile —
            # pozisyon kapanamıyorsa stop kalmalı, kapanıyorsa gereksiz)
            try:
                self.cancel_venue_stop(sym)
            except Exception:
                pass
        if not reduce_only:
            # GERCEK HESAP: kaldıraç+izole ayarı BAŞARISIZSA emir HİÇ açılmaz
            # (yanlış modda CROSS pozisyon açılmasın) — raise -> MirrorResult
            # ok=False -> paper muhasebe etkilenmez (fail-open sözleşme).
            self._ensure_lev_isolated(sym)
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
                    # Z4-4: bump hedefi tabanın %0.1 üstü (MARKET kayma payı).
                    # EN KÜÇÜK GEÇERLİ step katını seç; final notional
                    # invariantını (>= min_notional) doğrulama döngüsüyle
                    # garanti et. _quantize 'up' ile çağrılır → bump asla
                    # taban yuvarlamasıyla geri alınmaz.
                    step = self._lot_step.get(sym, 0.0)
                    if step > 0:
                        target = min_notional * _BUMP_HEADROOM
                        n = max(1, int(math.ceil(target / (step * price) - 1e-9)))
                        used_qty = n * step
                        guard = 0
                        while used_qty * price < min_notional and guard < 1000:
                            n += 1
                            used_qty = n * step
                            guard += 1
                    else:
                        used_qty = (min_notional / price) * 1.001
                    bumped = True
            # KAPANIŞ: ön-dust-skip YOK (mainnet reduce-only min-notional'dan
            # muaftır; testnette -4164 verirse aşağıda yakalanıp skip olur).
        except Exception:
            pass  # fiyata erişilemezse paper qty ile dene (borsa reddederse
                  # MirrorResult ok=False olur — fail-open sözleşmesi)
        if not reduce_only and self.max_notional_usdt > 0:
            # GERCEK HESAP cap: bump'li/bump'siz HER girişte final notional
            # tavanı aşılırsa emir HİÇ açılmaz (paper pozisyonu yine açılır,
            # exchange_verified=False ile dürüstçe işaretlenir).
            try:
                _px = self.get_price(sym)
            except Exception:
                _px = 0.0
            if _px > 0 and used_qty * _px > self.max_notional_usdt:
                print(f"[kaos-exec] NOTIONAL_CAP: {sym} notional "
                      f"{used_qty * _px:.2f} > tavan "
                      f"{self.max_notional_usdt:.2f} USDT — emir açılmadı",
                      flush=True)
                self._remember_error(f"NOTIONAL_CAP {sym}: "
                                     f"{used_qty * _px:.2f} > "
                                     f"{self.max_notional_usdt:.2f}")
                return {"ok": True, "order_id": None,
                        "status": "NOTIONAL_CAP_SKIPPED", "avg_price": None,
                        "qty_used": float(used_qty), "bumped": bumped,
                        "skipped": "notional_cap"}
        self._oid_seq += 1
        params: dict[str, Any] = {
            "symbol": sym,
            "side": "BUY" if side.upper().startswith("B") else "SELL",
            "type": "MARKET",
            "quantity": self._quantize(sym, used_qty,
                                       direction="up" if bumped else "down"),
            "newOrderRespType": "RESULT",
            # çift-emir koruması: ağ-retry aynı emri yeniden gönderirse
            # Binance aynı clientOrderId'yi reddeder (idempotency kalkanı);
            # seq soneki aynı milisaniyedeki iki emrin çakışmasını önler
            "newClientOrderId": f"kaosm{int(time.time() * 1000)}"
                                f"{self._oid_seq % 10000:04d}",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        try:
            resp = self._request("POST", "/fapi/v1/order", params)
        except Exception as exc:
            msg = str(exc)
            if reduce_only and "-4164" in msg:
                # testnet toz kapanış reddi: emri tekrar tekrar deneme
                print(f"[kaos-exec] DUST_SKIP: {sym} reduce-only -4164 "
                      f"(toz kapanamaz) — skip", flush=True)
                self._remember_error(f"DUST_SKIP {sym}: -4164")
                return {"ok": True, "order_id": None,
                        "status": "DUST_SKIPPED", "avg_price": None,
                        "qty_used": float(used_qty), "bumped": False,
                        "skipped": "dust"}
            raise
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
    Kapalıyken runner davranışı bit-özdeş kalır (hiçbir import yan etkisi yok).

    Ağ seçimi (kullanıcı kararı 09-07: KAOS GERÇEK hesapta):
      KAOS_EXCHANGE_NETWORK=mainnet           -> GERÇEK futures (fapi.binance.com)
      KAOS_EXCHANGE_TESTNET=1                 -> testnet (eski davranış, geriye dönük)
    Kaldıraç: KAOS_EXCHANGE_LEVERAGE (1..20, varsayılan 5; izole marj zorunlu).
    Notional tavanı: KAOS_EXCHANGE_MAX_NOTIONAL (USDT; bumplı giriş bunu
    aşarsa emir açılmaz — küçük gerçek hesapta büyütülmüş pozisyon koruması)."""
    network = str(os.environ.get("KAOS_EXCHANGE_NETWORK", "")).strip().lower()
    testnet_flag = str(os.environ.get("KAOS_EXCHANGE_TESTNET", "")).strip().lower() in ("1", "true")
    key = os.environ.get("BINANCE_API_KEY", "").strip()
    secret = os.environ.get("BINANCE_API_SECRET", "").strip()
    if network == "mainnet":
        enabled, host = True, HOST_MAINNET
    elif testnet_flag:
        enabled, host = True, HOST_DEFAULT
    else:
        return None
    if not (enabled and key and secret):
        return None
    try:
        lev = int(str(os.environ.get("KAOS_EXCHANGE_LEVERAGE", "5")).strip() or 5)
    except ValueError:
        lev = 5
    lev = max(1, min(20, lev))
    try:
        cap = float(str(os.environ.get("KAOS_EXCHANGE_MAX_NOTIONAL", "25")).strip() or 25)
    except ValueError:
        cap = 25.0
    if network == "mainnet":
        print(f"[kaos-exec] *** GERCEK HESAP MIRROR ENABLED *** host={host} "
              f"leverage={lev}x (izole) max_notional={cap:.2f} USDT", flush=True)
    else:
        print(f"[kaos-exec] testnet mirror ENABLED (futures testnet, "
              f"yalnız testnet hostuna emir) leverage={lev}x", flush=True)
    return TestnetExecutor(key, secret, host=host, leverage=lev,
                           max_notional_usdt=cap)
