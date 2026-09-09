# src/entropy/venues/alpaca_equities.py
"""AlpacaEquitiesAdapter — NASDAQ venue for the KAOS multibot (stdlib-only).

HTTP discipline mirrors ``scripts/kaos_testnet_exec.py``: ``urllib.request``
with hard timeouts, bounded retries with backoff, key/IP-safe error messages,
a last-5 error ring, and a 429-aware "banned until" gate (``banned_until_ms``/
``is_banned`` accessors keep the crypto ban contract). NO new dependencies, NO
ccxt, NO alpaca-py SDK.

HOSTS / HONESTY (paper by default):
- Trading base URL defaults to ``https://paper-api.alpaca.markets``.
- Live host ``https://api.alpaca.markets`` is selected ONLY when env
  ``KAOS_ALPACA_LIVE=1`` AND :meth:`connect_test` (GET /v2/account with the
  configured keys) succeeded THIS BOOT — see :meth:`from_env`. Otherwise the
  adapter stays on paper, honestly.
- Keys come from env ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``. With no
  keys the executor is INERT: ``available`` is False and every order method
  raises :class:`entropy.venues.base.VenueUnavailable` (fills can never be
  faked live). Keyless Yahoo bars stay reachable (market data tier).

EQUITY MECHANICS (survey-equities-feasibility.md §D):
- Bracket = venue STOP order whose qty is COMPUTED from the open position at
  placement (Alpaca has no ``closePosition=true`` Binance-ism). GAP RISK: a
  stop-market on equities fills at the next print after the trigger — an
  overnight/weekend gap can fill far below (long) / above (short) the stop
  price, with no mark-price protection. Documented; accepted.
- Take-profit = GTC LIMIT order on the closing side (no reduce-only flag
  exists; closing is an opposite-side order — close-only semantics are
  enforced bot-side by reading open positions before every close order).
- ``extended_hours=false`` on every order: RTH-only by construction.
- Rounding: prices to the $0.01 tick (stop trigger rounds SAFE: long SL down /
  short SL up, mirroring crypto ``_round_stop``), qty floored to whole shares.
- Leverage forced 1.0 (RegT reality). No funding concept:
  :meth:`get_funding_rate` always returns ``None`` so the M2 funding gate
  no-ops on equities.
- PDT gate: with account equity < $25,000, max 3 day-trades per rolling 5
  trading days — enforced via an injected :class:`PdtCounter` (the SUPERVISOR
  owns the counter file path; the adapter only reads/records it).
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from entropy.venues.base import VenueError, VenueUnavailable
from entropy.venues.clock import USEquitiesClock

PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"
DATA_HOST = "https://data.alpaca.markets"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

_TICK = 0.01
_TIMEOUT_S = 10.0
_RETRY_BACKOFF_START_S = 1.5
_RETRY_BACKOFF_MAX_S = 15.0
_ALPACA_BACKOFF_START_S = 2.0
_ALPACA_BACKOFF_MAX_S = 60.0

_PDT_MIN_EQUITY_USD = 25000.0
_PDT_MAX_DAY_TRADES = 3
_PDT_WINDOW_TRADING_DAYS = 5

_MIN_ENTRY_NOTIONAL_USD = 1.0  # Alpaca rejects sub-$1 orders

_ORDER_TYPES = ("market", "limit", "stop", "stop_limit")
# Alpaca statuses meaning "still working" (orders Alive check).
_OPEN_STATUSES = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding",
    "partially_filled", "held",
})

_TF_TO_ALPACA = {"1m": "1Min", "5m": "5Min", "15m": "15Min",
                 "30m": "30Min", "1h": "1Hour", "1d": "1Day"}
_TF_TO_YAHOO = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
                "1h": "60m", "1d": "1d"}
_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
          "1h": 3_600_000, "1d": 86_400_000}

_ET = ZoneInfo("America/New_York")


# Broker-boundary symbol fixes (L-4, review R1/R2): the frozen universe
# carries Entropy's "BRKB" verbatim; Alpaca's real ticker is "BRK.B". The
# map applies at clean_symbol — the ONE choke point every order/position/
# bars call passes through — so the paper book, wire orders and position
# matching all see the SAME (broker-correct) symbol.
_ALPACA_SYMBOL_FIX = {"BRKB": "BRK.B"}


def clean_symbol(symbol: str) -> str:
    """'nasdaq:AAPL' / 'binance-spot:ETHUSDT' / 'aapl' -> 'AAPL' (with
    broker-format fixes applied, see _ALPACA_SYMBOL_FIX)."""
    sym = str(symbol).split(":", 1)[-1].upper()
    return _ALPACA_SYMBOL_FIX.get(sym, sym)


def mask_key(text: str, api_key: str) -> str:
    if api_key and api_key in text:
        return text.replace(api_key, "****")
    return text


class PdtCounter:
    """Persisted day-trade counter for the FINRA PDT gate.

    The SUPERVISOR owns the JSON file path (injected here); the adapter only
    consults :meth:`can_open_trade` and records entries via
    :meth:`record_fill`. A "fill" is recorded on ENTRY (conservative proxy for
    a day-trade: an entry+exit in one session counts once, and an overnight
    position exit recorded the next window ages out honestly — never
    undercounts below the rule, may occasionally overcount).

    Window math: the most recent 5 TRADING DAYS per the crocodile
    ``USMarketCalendar`` (weekends AND holidays skip, so a fill on Tuesday
    before a Wednesday holiday is still inside a window evaluated Friday).
    Gate: max 3 recorded fills within the window.
    """

    def __init__(self, path: str | Path, calendar: Any | None = None) -> None:
        self.path = Path(path)
        self.calendar = calendar

    def _cal(self) -> Any:
        if self.calendar is None:
            from crocodile.core.scheduler.calendar import USMarketCalendar

            self.calendar = USMarketCalendar()
        return self.calendar

    # ---- persistence (atomic tmp+replace, mirrors runner state discipline)
    def _load(self) -> list[int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            fills = data.get("fills") if isinstance(data, dict) else None
            return [int(x) for x in fills] if isinstance(fills, list) else []
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001 — corrupt file: start empty, don't crash
            return []

    def _save(self, fills: list[int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"fills": fills}), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- trading-day window -------------------------------------------------
    def _window_days(self, now_ms: int, count: int = _PDT_WINDOW_TRADING_DAYS) -> set:
        """The `count` most recent trading days up to today (ET), inclusive."""
        cal = self._cal()
        d = datetime.fromtimestamp(now_ms / 1000.0, tz=UTC).astimezone(_ET).date()
        days: set = set()
        guard = 0
        while len(days) < count and guard < 400:
            if cal.is_trading_day(d):
                days.add(d)
            d = date.fromordinal(d.toordinal() - 1)
            guard += 1
        return days

    def count_in_window(self, now_ms: int) -> int:
        days = self._window_days(now_ms)
        n = 0
        for ms in self._load():
            day = datetime.fromtimestamp(ms / 1000.0, tz=UTC) \
                .astimezone(_ET).date()
            if day in days:
                n += 1
        return n

    def can_open_trade(self, now_ms: int) -> bool:
        """True while fewer than 3 recorded day-trades sit in the window."""
        return self.count_in_window(now_ms) < _PDT_MAX_DAY_TRADES

    def record_fill(self, now_ms: int) -> None:
        fills = self._load()
        fills.append(int(now_ms))
        fills.sort()
        self._save(fills)


class AlpacaEquitiesAdapter:
    """VenueAdapter for US equities (NASDAQ) via Alpaca REST v2.

    Method names/signatures are byte-identical to the crypto
    ``TestnetExecutor`` surface (see ``entropy.venues.base``) so the live
    runner's getattr feature-detects work unchanged.
    """

    venue_id = "alpaca-nasdaq"

    def __init__(self, api_key_id: str | None = None,
                 api_secret: str | None = None, *,
                 base_url: str = PAPER_HOST,
                 data_host: str = DATA_HOST,
                 transport: Any | None = None,
                 clock: USEquitiesClock | None = None,
                 pdt_counter: PdtCounter | None = None,
                 sizing_pct: float = 0.0,
                 slots: int = 1,
                 leverage: float = 1.0,
                 max_notional_usdt: float = 0.0) -> None:
        self.api_key_id = api_key_id or ""
        self.api_secret = api_secret or ""
        self._base_url = base_url.rstrip("/")
        self.data_host = data_host.rstrip("/")
        self._transport = transport  # injectable for zero-network tests
        self.clock = clock if clock is not None else USEquitiesClock()
        self.pdt_counter = pdt_counter
        # RegT: NO leverage on equities — forced 1.0 regardless of argument.
        self.leverage = 1.0
        self.max_notional_usdt = float(max_notional_usdt or 0.0)
        self.sizing_pct = float(sizing_pct or 0.0)
        self.slots = max(1, min(10, int(slots or 1)))
        self._stop_orders: dict[str, str] = {}
        self._tp_orders: dict[str, str] = {}
        self._oid_seq = 0
        self._banned_until = 0.0        # epoch seconds (429 backoff)
        self._alpaca_backoff_s = _ALPACA_BACKOFF_START_S
        self.last_errors: deque = deque(maxlen=5)
        self.connect_note = "paper" if self.available else "PAPER (no Alpaca keys)"

    # ---- factory -------------------------------------------------------------
    @classmethod
    def from_env(cls, *, transport: Any | None = None,
                 env: Any = None) -> AlpacaEquitiesAdapter:
        """Build from env. Keys: APCA_API_KEY_ID / APCA_API_SECRET_KEY.
        Live host requires KAOS_ALPACA_LIVE=1 AND a passing connect-test THIS
        BOOT; anything less keeps paper, honestly (connect_note records why).
        Optional: KAOS_ALPACA_SIZING_PCT (percent), KAOS_ALPACA_SLOTS,
        KAOS_ALPACA_MAX_NOTIONAL, KAOS_ALPACA_PDT_PATH (PdtCounter JSON file —
        supervisor-owned path).
        """
        env = env if env is not None else os.environ
        key = str(env.get("APCA_API_KEY_ID", "")).strip()
        secret = str(env.get("APCA_API_SECRET_KEY", "")).strip()

        def _f(name: str, default: float) -> float:
            try:
                return float(str(env.get(name, "")).strip() or default)
            except ValueError:
                return default

        def _i(name: str, default: int) -> int:
            try:
                return int(str(env.get(name, "")).strip() or default)
            except ValueError:
                return default

        pdt_path = str(env.get("KAOS_ALPACA_PDT_PATH", "")).strip()
        adapter = cls(
            key, secret, transport=transport,
            sizing_pct=_f("KAOS_ALPACA_SIZING_PCT", 0.0) / 100.0,
            slots=max(1, min(10, _i("KAOS_ALPACA_SLOTS", 1))),
            max_notional_usdt=_f("KAOS_ALPACA_MAX_NOTIONAL", 0.0),
            pdt_counter=PdtCounter(pdt_path) if pdt_path else None,
        )
        live_requested = str(env.get("KAOS_ALPACA_LIVE", "")).strip().lower() \
            in ("1", "true")
        if adapter.available and live_requested:
            ok, detail = adapter.connect_test(base_url=LIVE_HOST)
            if ok:
                adapter._base_url = LIVE_HOST
                adapter.connect_note = "LIVE (connect-test OK this boot)"
            else:
                adapter.connect_note = f"paper (live gate failed: {detail})"
        elif live_requested and not adapter.available:
            adapter.connect_note = "paper (KAOS_ALPACA_LIVE set but no keys)"
        return adapter

    # ---- honesty surface -------------------------------------------------------
    @property
    def available(self) -> bool:
        """False = INERT executor (no keys): orders raise, never pretend."""
        return bool(self.api_key_id and self.api_secret)

    @property
    def host(self) -> str:
        return self._base_url

    @property
    def is_live(self) -> bool:
        return self._base_url == LIVE_HOST

    def connect_test(self, base_url: str | None = None) -> tuple[bool, str]:
        """Read-only GET /v2/account with the configured keys.

        Returns (ok, detail); detail is key-masked and safe for banners/state.
        This is the ONLY gate that may ever promote the adapter to the live
        host (and from_env does it per-boot, never persisted).
        """
        if not self.available:
            return False, "no API keys"
        try:
            acct = self._request("GET", "/v2/account", host=base_url,
                                 tries=1)
            equity = float(acct.get("equity") or 0.0)
            return True, f"account ok, equity={equity:.2f} USD"
        except Exception as exc:  # noqa: BLE001 — connect failure = paper
            self._remember_error(str(exc))
            return False, mask_key(str(exc)[:160], self.api_key_id)

    # ---- low level --------------------------------------------------------------
    def _remember_error(self, msg: str) -> None:
        self.last_errors.append(mask_key(str(msg)[:200], self.api_key_id))

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.api_secret,
            "User-Agent": "kaos-multibot/1.0",
        }

    def _send(self, method: str, url: str, body: bytes | None,
              headers: dict[str, str]) -> tuple[int, Any]:
        """One HTTP attempt. Returns (status, parsed_json); status 0 = a
        network-level failure (transport-injectable for zero-network tests).
        """
        if self._transport is not None:
            return self._transport.request(method, url, headers=headers,
                                           body=body)
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return int(resp.status), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8", "replace"))
            except ValueError:
                payload = {}
            return int(exc.code), payload
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0, {}

    def _register_backoff(self) -> None:
        self._banned_until = time.time() + self._alpaca_backoff_s
        self._alpaca_backoff_s = min(self._alpaca_backoff_s * 2.0,
                                     _ALPACA_BACKOFF_MAX_S)

    def is_banned(self, now_ms: float | None = None) -> bool:
        """429 backoff active? (crypto ban contract, Alpaca flavor)."""
        now = float(now_ms) if now_ms is not None else time.time() * 1000.0
        return bool(self._banned_until and now < self._banned_until * 1000.0)

    def banned_until_ms(self) -> int:
        return int(self._banned_until * 1000.0) if self._banned_until else 0

    def _request(self, method: str, path: str, params: dict | None = None,
                 json_body: dict | None = None, host: str | None = None,
                 tries: int = 2) -> Any:
        """Retrying request. Network errors (status 0) and 5xx retry with
        backoff; 429 registers a banned-until backoff and raises; other HTTP
        errors raise immediately (protocol errors are not retried)."""
        if self.is_banned():
            raise VenueError(429, "alpaca paused until "
                             f"{datetime.fromtimestamp(self._banned_until, tz=UTC).isoformat()}"
                             " (429 backoff)")
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{(host or self._base_url).rstrip('/')}{path}{query}"
        body = (json.dumps(json_body).encode("utf-8")
                if json_body is not None else None)
        headers = self._headers()
        if body is not None:
            headers["Content-Type"] = "application/json"
        backoff = _RETRY_BACKOFF_START_S
        last_status = 0
        for attempt in range(max(1, tries)):
            status, payload = self._send(method, url, body, headers)
            if 200 <= status < 300:
                self._alpaca_backoff_s = _ALPACA_BACKOFF_START_S
                return payload
            last_status = status
            if status == 429:
                self._register_backoff()
                msg = str(payload.get("message") or "rate limited")
                self._remember_error(f"429: {msg}")
                raise VenueError(429, mask_key(msg, self.api_key_id))
            if status in (0,) or status >= 500:
                if attempt + 1 < tries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2.0, _RETRY_BACKOFF_MAX_S)
                continue
            msg = str(payload.get("message") or payload)[:200]
            self._remember_error(f"{status}: {msg}")
            raise VenueError(status, mask_key(msg, self.api_key_id))
        err = ("network unreachable: "
               f"status={last_status} after {tries} attempts")
        self._remember_error(err)
        raise VenueError(None, err) from None

    # ---- rounding (per-venue filters: tick 0.01, whole shares) ------------------
    @staticmethod
    def round_tick(price: float, direction: str = "down") -> float:
        """Round to the $0.01 grid (direction 'down'|'up'; float-safe)."""
        px = float(price)
        n = px / _TICK
        steps = math.floor(n + 1e-9) if direction == "down" else math.ceil(n - 1e-9)
        return round(steps * _TICK, 10)

    @staticmethod
    def round_qty(qty: float) -> int:
        """Whole shares, floor (fractionals are a different order kind)."""
        return int(math.floor(float(qty) + 1e-9))

    def _round_stop(self, pos_side: str, price: float) -> float:
        # long SL DOWN / short SL UP — trigger never fires LATE (crypto parity)
        return self.round_tick(price, "down" if pos_side == "long" else "up")

    def _round_tp(self, pos_side: str, price: float) -> float:
        # long TP UP / short TP DOWN — limit never crosses the wrong side
        return self.round_tick(price, "up" if pos_side == "long" else "down")

    def _next_cid(self, kind: str) -> str:
        self._oid_seq += 1
        return f"KAOS-{kind}{int(time.time() * 1000)}{self._oid_seq % 10000:04d}"

    # ---- market hours -------------------------------------------------------------
    def is_market_open(self, now_ms: int) -> bool:
        return bool(self.clock.is_open(now_ms))

    def session_label(self, now_ms: int) -> str:
        return str(self.clock.session_label(now_ms))

    # ---- positions helper ----------------------------------------------------------
    def _position_for(self, sym: str) -> dict | None:
        for pos in self.get_open_positions():
            if clean_symbol(str(pos.get("symbol") or "")) == sym:
                return pos
        return None

    # ---- orders ------------------------------------------------------------------------
    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "market", time_in_force: str = "day",
                    limit_price: float | None = None,
                    stop_price: float | None = None,
                    client_order_id: str | None = None,
                    extended_hours: bool = False) -> dict:
        """POST /v2/orders — market/limit/stop/stop_limit, RTH-only.

        Returns the raw Alpaca order response. ``qty`` is floored to whole
        shares; prices formatted on the $0.01 grid; client ids are KAOS-
        prefixed. This is the generic primitive; ``place_market_order`` /
        ``place_venue_stop`` / ``place_venue_tp`` build on it.
        """
        if not self.available:
            raise VenueUnavailable(401, "alpaca executor inert: no API keys "
                                        "(APCA_API_KEY_ID/APCA_API_SECRET_KEY)")
        order_type = str(order_type).lower()
        if order_type not in _ORDER_TYPES:
            raise VenueError(400, f"unsupported order type {order_type!r}")
        shares = self.round_qty(qty)
        if shares < 1:
            raise VenueError(400, f"qty {qty} rounds to 0 whole shares")
        body: dict[str, Any] = {
            "symbol": clean_symbol(symbol),
            "qty": str(shares),
            "side": "buy" if str(side).lower().startswith("b") else "sell",
            "type": order_type,
            "time_in_force": str(time_in_force).lower(),
            "client_order_id": client_order_id or self._next_cid("X"),
            "extended_hours": bool(extended_hours),
        }
        if order_type in ("limit", "stop_limit") and limit_price is not None:
            body["limit_price"] = f"{self.round_tick(limit_price):.2f}"
        if order_type in ("stop", "stop_limit") and stop_price is not None:
            body["stop_price"] = f"{self.round_tick(stop_price):.2f}"
        return self._request("POST", "/v2/orders", json_body=body)

    def _gate_entry_pdt(self, now_ms: int) -> dict | None:
        """PDT skip envelope when the <$25k gate blocks a new day-trade."""
        if self.pdt_counter is None:
            return None
        try:
            equity = float(self.get_balance().get("wallet") or 0.0)
        except Exception:  # noqa: BLE001 — equity unreadable = gate ON (safe side)
            equity = 0.0
        if equity >= _PDT_MIN_EQUITY_USD:
            return None
        if self.pdt_counter.can_open_trade(now_ms):
            return None
        self._remember_error(
            f"PDT_BLOCKED: equity {equity:.2f} < {_PDT_MIN_EQUITY_USD:.0f} "
            "and 3 day-trades in rolling 5 trading days")
        return {"ok": True, "order_id": None, "status": "PDT_BLOCKED",
                "avg_price": None, "qty_used": 0.0, "bumped": False,
                "skipped": "pdt"}

    def place_market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> dict:
        """MARKET order -> the crypto result envelope.

        Entries (reduce_only=False) may be SKIPPED (ok:True + skipped:...):
        pdt / margin / notional_cap / min_notional — the order is NOT sent.
        Closes (reduce_only=True) enforce close-only semantics by reading the
        open position first: flat -> ALREADY_CLOSED; qty clamped to the real
        position; sub-share remainder -> DUST_SKIPPED. Close-side mismatches
        (reduce_only order that would INCREASE exposure) raise — honest, the
        runner's fail-open handlers absorb it.
        """
        sym = clean_symbol(symbol)
        if not self.available:
            raise VenueUnavailable(401, "alpaca executor inert: no API keys "
                                        "(APCA_API_KEY_ID/APCA_API_SECRET_KEY)")
        is_buy = str(side).upper().startswith("B")
        if reduce_only:
            pos = self._position_for(sym)
            if pos is None:
                return {"ok": True, "order_id": None,
                        "status": "ALREADY_CLOSED", "avg_price": None,
                        "qty_used": float(qty), "bumped": False,
                        "skipped": "already_closed"}
            pos_side = str(pos.get("side") or "long")
            if (pos_side == "long") == is_buy:
                raise VenueError(422, f"reduce_only close-side mismatch on "
                                      f"{sym}: position {pos_side}, side "
                                      f"{'buy' if is_buy else 'sell'}")
            pos_qty = float(pos.get("qty") or pos.get("contracts") or 0.0)
            shares = self.round_qty(min(float(qty), pos_qty))
            if shares < 1:
                return {"ok": True, "order_id": None,
                        "status": "DUST_SKIPPED", "avg_price": None,
                        "qty_used": float(qty), "bumped": False,
                        "skipped": "dust"}
            resp = self.place_order(sym, side, shares, order_type="market",
                                    time_in_force="day",
                                    client_order_id=self._next_cid("M"))
            return {"ok": True, "order_id": str(resp.get("id") or ""),
                    "status": str(resp.get("status") or "?"),
                    "avg_price": (float(resp["filled_avg_price"])
                                  if resp.get("filled_avg_price") else None),
                    "qty_used": float(shares), "bumped": False}
        # ---- ENTRY ----
        blocked = self._gate_entry_pdt(int(time.time() * 1000))
        if blocked is not None:
            return blocked
        used_qty, bumped = float(qty), False
        if self.sizing_pct > 0:
            try:
                bal = self.get_balance()
                wallet = float(bal.get("wallet") or 0.0)
                available = float(bal.get("available") or 0.0)
                price = self.get_price(sym)
                if wallet > 0 and price > 0:
                    target = wallet * self.sizing_pct * self.leverage / self.slots
                    if available > 0:
                        target = min(target, available * self.leverage * 0.95)
                    shares = self.round_qty(target / price)
                    if shares < 1:
                        self._remember_error(
                            f"MARGIN_SKIP {sym}: target {target:.2f} "
                            f"< 1 share @ {price}")
                        return {"ok": True, "order_id": None,
                                "status": "MARGIN_SKIPPED", "avg_price": None,
                                "qty_used": used_qty, "bumped": False,
                                "skipped": "margin"}
                    used_qty, bumped = float(shares), True
            except Exception:  # noqa: BLE001 — sizing degraded: paper qty
                pass           # passes (crypto fail-open parity)
        price_now = 0.0
        try:
            price_now = float(self.get_price(sym))
        except Exception:  # noqa: BLE001
            price_now = 0.0
        cap = self.max_notional_usdt
        if price_now > 0 and used_qty * price_now < _MIN_ENTRY_NOTIONAL_USD:
            self._remember_error(f"MIN_NOTIONAL {sym}: "
                                 f"{used_qty * price_now:.2f} < "
                                 f"{_MIN_ENTRY_NOTIONAL_USD:.2f}")
            return {"ok": True, "order_id": None,
                    "status": "MIN_NOTIONAL_SKIPPED", "avg_price": None,
                    "qty_used": used_qty, "bumped": bumped,
                    "skipped": "min_notional"}
        if price_now > 0 and cap > 0 and used_qty * price_now > cap:
            self._remember_error(f"NOTIONAL_CAP {sym}: "
                                 f"{used_qty * price_now:.2f} > {cap:.2f}")
            return {"ok": True, "order_id": None,
                    "status": "NOTIONAL_CAP_SKIPPED", "avg_price": None,
                    "qty_used": used_qty, "bumped": bumped,
                    "skipped": "notional_cap"}
        shares = self.round_qty(used_qty)
        if shares < 1:
            return {"ok": True, "order_id": None,
                    "status": "MARGIN_SKIPPED", "avg_price": None,
                    "qty_used": used_qty, "bumped": bumped,
                    "skipped": "margin"}
        resp = self.place_order(sym, side, shares, order_type="market",
                                time_in_force="day",
                                client_order_id=self._next_cid("M"))
        # PDT counter: record the entry fill (caller-injected file, but the
        # adapter is the natural recording point for venue-truthful counters).
        if self.pdt_counter is not None:
            try:
                self.pdt_counter.record_fill(int(time.time() * 1000))
            except Exception:  # noqa: BLE001 — counter failure never blocks
                self._remember_error("pdt record_fill failed")
        return {"ok": True, "order_id": str(resp.get("id") or ""),
                "status": str(resp.get("status") or "?"),
                "avg_price": (float(resp["filled_avg_price"])
                              if resp.get("filled_avg_price") else None),
                "qty_used": float(shares), "bumped": bumped}

    def _closing_side(self, pos_side: str) -> str:
        return "sell" if pos_side == "long" else "buy"

    def place_venue_stop(self, sym: str, pos_side: str,
                         stop_price: float) -> str | None:
        """Venue STOP order protecting the open position (qty computed from
        the position — GAP RISK: equity stop-market fills at the next print
        past the trigger, an overnight gap can fill far away; no mark-price
        protection exists on equities. GTC so the bracket survives restarts
        like the crypto venue stop). Best-effort: any failure logs to the
        error ring and returns None (paper-side SL keeps running).
        """
        sym = clean_symbol(sym)
        if not stop_price or float(stop_price) <= 0:
            return None
        if not self.available:
            raise VenueUnavailable(401, "alpaca executor inert: no API keys")
        with contextlib.suppress(Exception):  # sweep best-effort (crypto parity)
            self.sweep_stale_stops(sym, pos_side)
        try:
            pos = self._position_for(sym)
            if pos is None:
                self._remember_error(f"venue stop {sym}: no open position")
                return None
            pos_qty = float(pos.get("qty") or pos.get("contracts") or 0.0)
            shares = self.round_qty(pos_qty)
            if shares < 1:
                self._remember_error(f"venue stop {sym}: qty {pos_qty} < 1")
                return None
            trigger = self._round_stop(pos_side, float(stop_price))
            resp = self.place_order(
                sym, self._closing_side(pos_side), shares, order_type="stop",
                time_in_force="gtc", stop_price=trigger,
                client_order_id=self._next_cid("S"))
            oid = str(resp.get("id") or "")
            if oid:
                self._stop_orders[sym] = oid
            return oid or None
        except Exception as exc:  # noqa: BLE001 — best-effort bracket leg
            self._remember_error(f"venue stop FAILED {sym}: {exc}")
            return None

    def place_venue_tp(self, sym: str, pos_side: str, qty: float,
                       tp_price: float) -> str | None:
        """GTC LIMIT take-profit on the closing side (no reduce-only concept
        on equities — closing is an opposite-side order; qty is computed
        bot-side). Best-effort like the crypto venue TP."""
        sym = clean_symbol(sym)
        q = float(qty or 0)
        if not tp_price or float(tp_price) <= 0 or q <= 0:
            return None
        if not self.available:
            raise VenueUnavailable(401, "alpaca executor inert: no API keys")
        shares = self.round_qty(q)
        if shares < 1:
            self._remember_error(f"venue tp {sym}: qty {q} < 1 share")
            return None
        price = self._round_tp(pos_side, float(tp_price))
        try:
            resp = self.place_order(
                sym, self._closing_side(pos_side), shares, order_type="limit",
                time_in_force="gtc", limit_price=price,
                client_order_id=self._next_cid("T"))
            oid = str(resp.get("id") or "")
            if oid:
                self._tp_orders[sym] = oid
            return oid or None
        except Exception as exc:  # noqa: BLE001 — best-effort bracket leg
            self._remember_error(f"venue tp FAILED {sym}: {exc}")
            return None

    # ---- bracket bookkeeping -------------------------------------------------------------
    def _open_orders(self, sym: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"status": "open", "limit": 500}
        if sym:
            params["symbols"] = clean_symbol(sym)
        rows = self._request("GET", "/v2/orders", params=params)
        return [r for r in (rows if isinstance(rows, list) else [])
                if isinstance(r, dict)]

    def cancel_venue_stop(self, sym: str) -> bool:
        sym = clean_symbol(sym)
        oid = self._stop_orders.pop(sym, None)
        if not oid:
            return False
        return self.cancel_stop_by_id(sym, oid)

    def cancel_stop_by_id(self, sym: str, order_id: str) -> bool:
        """DELETE /v2/orders/{id}. 404/422 = already gone = success (mirrors
        crypto -2011 tolerance: the bracket is unneeded once the order died).
        """
        try:
            self._request("DELETE", f"/v2/orders/{order_id}", tries=1)
            return True
        except VenueError as exc:
            if exc.code in (404, 422):
                return True
            self._remember_error(f"cancel stop {sym} {order_id}: {exc.msg}")
            return False
        except Exception as exc:  # noqa: BLE001
            self._remember_error(f"cancel stop {sym} {order_id}: {exc}")
            return False

    def cancel_stale_venue_stops(self, symbols: Any = None) -> int:
        syms = list(symbols) if symbols else list(self._stop_orders)
        n = 0
        for sym in syms:
            if self.cancel_venue_stop(sym):
                n += 1
        return n

    def cancel_venue_tp(self, sym: str) -> bool:
        sym = clean_symbol(sym)
        oid = self._tp_orders.pop(sym, None)
        if not oid:
            return False
        try:
            self._request("DELETE", f"/v2/orders/{oid}", tries=1)
            return True
        except VenueError as exc:
            if exc.code in (404, 422):
                return True
            self._remember_error(f"cancel tp {sym}: {exc.msg}")
            return False
        except Exception as exc:  # noqa: BLE001
            self._remember_error(f"cancel tp {sym}: {exc}")
            return False

    def adopt_open_stop(self, sym: str, pos_side: str = "long") -> str | None:
        """Adopt an open STOP/STOP_LIMIT order on the CLOSING side for this
        symbol (restart-safe rearm: adopt instead of double-placing)."""
        sym = clean_symbol(sym)
        want = self._closing_side(pos_side)
        try:
            for row in self._open_orders(sym):
                if str(row.get("type") or "").lower() not in ("stop",
                                                              "stop_limit"):
                    continue
                if str(row.get("side") or "").lower() != want:
                    continue
                oid = str(row.get("id") or "")
                if oid:
                    self._stop_orders[sym] = oid
                    return oid
        except Exception as exc:  # noqa: BLE001
            self._remember_error(f"adopt stop {sym}: {exc}")
        return None

    def adopt_open_tp(self, sym: str, pos_side: str) -> str | None:
        """Adopt an open GTC LIMIT on the closing side (our TPs are the only
        closing-side GTC limits this bot places; KAOS-T client ids win)."""
        sym = clean_symbol(sym)
        want = self._closing_side(pos_side)
        try:
            fallback: str | None = None
            for row in self._open_orders(sym):
                if str(row.get("type") or "").lower() != "limit":
                    continue
                if str(row.get("side") or "").lower() != want:
                    continue
                oid = str(row.get("id") or "")
                if not oid:
                    continue
                if str(row.get("client_order_id") or "").startswith("KAOS-"):
                    self._tp_orders[sym] = oid
                    return oid
                fallback = fallback or oid
            if fallback:
                self._tp_orders[sym] = fallback
            return fallback
        except Exception as exc:  # noqa: BLE001
            self._remember_error(f"adopt tp {sym}: {exc}")
            return None

    def algo_order_alive(self, sym: str, algoid: str) -> bool:
        return self.order_alive(sym, algoid)

    def order_alive(self, sym: str, order_id: str) -> bool:
        """GET /v2/orders/{id}; alive = still working (new/accepted/
        partially_filled/...). Any failure -> False (crypto parity)."""
        try:
            row = self._request("GET", f"/v2/orders/{order_id}", tries=1)
            return str(row.get("status") or "").lower() in _OPEN_STATUSES
        except Exception:  # noqa: BLE001 — dead or unreadable = not alive
            return False

    def sweep_stale_stops(self, sym: str, pos_side: str) -> int:
        """Cancel OPEN stop orders on the OPPOSITE of the new position side
        (a direction flip must not leave the old side's stop armed)."""
        sym = clean_symbol(sym)
        want_cancel = "buy" if pos_side == "long" else "sell"
        n = 0
        try:
            for row in self._open_orders(sym):
                if str(row.get("type") or "").lower() not in ("stop",
                                                              "stop_limit"):
                    continue
                if str(row.get("side") or "").lower() != want_cancel:
                    continue
                oid = str(row.get("id") or "")
                if oid and self.cancel_stop_by_id(sym, oid):
                    n += 1
        except Exception as exc:  # noqa: BLE001
            self._remember_error(f"sweep stops {sym}: {exc}")
        return n

    # ---- reads ---------------------------------------------------------------------------
    def get_balance(self) -> dict:
        """-> {wallet, available, unrealized, equity, pdt_eligible}.

        wallet = equity (sizing base), available = cash clamped >= 0,
        unrealized = account unrealized_pl. ``pdt_eligible`` exposes the
        $25k PDT threshold state for the supervisor's honesty surfaces.
        """
        acct = self._request("GET", "/v2/account")
        equity = float(acct.get("equity") or 0.0)
        cash = float(acct.get("cash") or 0.0)
        return {
            "wallet": equity,
            "available": max(0.0, cash),
            "unrealized": float(acct.get("unrealized_pl") or 0.0),
            "equity": equity,
            "pdt_eligible": equity >= _PDT_MIN_EQUITY_USD,
        }

    def get_open_positions(self) -> list[dict]:
        """-> [{symbol, contracts, side, qty, entry_price, mark_price,
        unrealized, notional, leverage, isolated}] — crypto position schema
        (leverage pinned 1.0, isolated always False on equities)."""
        rows = self._request("GET", "/v2/positions")
        out: list[dict] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            qty = float(row.get("qty") or 0.0)
            if qty == 0.0:
                continue
            out.append({
                "symbol": clean_symbol(str(row.get("symbol") or "")),
                "contracts": abs(qty),
                "side": "long" if qty > 0 else "short",
                "qty": abs(qty),
                "entry_price": float(row.get("avg_entry_price") or 0.0),
                "mark_price": float(row.get("current_price") or 0.0),
                "unrealized": float(row.get("unrealized_pl") or 0.0),
                "notional": abs(float(row.get("market_value") or 0.0)),
                "leverage": 1.0,
                "isolated": False,
            })
        return out

    def get_price(self, symbol: str) -> float:
        """Last trade via Alpaca MarketData v2 (data host, IEX feed)."""
        sym = clean_symbol(symbol)
        row = self._request("GET", f"/v2/stocks/{sym}/trades:latest",
                            params={"feed": "iex"}, host=self.data_host,
                            tries=1)
        trade = row.get("trade") if isinstance(row, dict) else {}
        return float((trade or {}).get("p") or 0.0)

    def get_funding_rate(self, symbol: str) -> None:
        """ALWAYS None: equities have no per-8h funding rate, so the M2
        funding gate no-ops on this venue (documented contract)."""
        return None

    # ---- bars (kline-shaped rows for the KAOS scanner) --------------------------------------
    def fetch_bars(self, symbol: str, timeframe: str = "15m",
                   limit: int = 200, end_ms: int | None = None) -> list[list]:
        """Ascending KLINE-SHAPED rows for `symbol` (Binance kline layout:
        [open_ms, open, high, low, close, vol, close_ms, quote_vol, 0, 0, 0,
        "0.0"] — exactly the shape scripts/entropy_accuracy_btc15m.build_ticks
        and LivePaper._bars_by_open consume: k[0] int ms, k[1..5] floats,
        k[6] int ms).

        Tier 1: Alpaca MarketData v2 bars (keyed, IEX free feed).
        Tier 2: Yahoo chart (keyless) — keeps the paper lane fed with no
        credentials at all. Both tiers fail -> VenueError (the runner's
        backoff wrapper treats it like any fetch failure).
        """
        if self.available:
            try:
                rows = self._alpaca_bars(symbol, timeframe, limit, end_ms)
                if rows:
                    return rows
            except Exception:  # noqa: BLE001 — fall through to Yahoo
                self._remember_error(f"alpaca bars {symbol} failed; "
                                     "trying Yahoo")
        return self._yahoo_bars(symbol, timeframe, limit, end_ms)

    def _alpaca_bars(self, symbol: str, timeframe: str, limit: int,
                     end_ms: int | None) -> list[list]:
        tf = _TF_TO_ALPACA.get(str(timeframe).lower())
        if tf is None:
            raise VenueError(400, f"unsupported timeframe {timeframe!r} "
                                  "for alpaca bars")
        params: dict[str, Any] = {"timeframe": tf, "limit": min(int(limit),
                                                               10000),
                                  "feed": "iex"}
        if end_ms:
            params["end"] = datetime.fromtimestamp(end_ms / 1000.0,
                                                   tz=UTC) \
                .strftime("%Y-%m-%dT%H:%M:%SZ")
        row = self._request("GET",
                            f"/v2/stocks/{clean_symbol(symbol)}/bars",
                            params=params, host=self.data_host, tries=1)
        out: list[list] = []
        for b in row.get("bars") or []:
            t = datetime.fromisoformat(str(b.get("t")).replace("Z", "+00:00"))
            out.append(self._kline_row(int(t.timestamp() * 1000.0),
                                       float(b.get("o") or 0.0),
                                       float(b.get("h") or 0.0),
                                       float(b.get("l") or 0.0),
                                       float(b.get("c") or 0.0),
                                       float(b.get("v") or 0.0),
                                       str(timeframe).lower()))
        return out

    def _yahoo_bars(self, symbol: str, timeframe: str, limit: int,
                    end_ms: int | None) -> list[list]:
        tf = str(timeframe).lower()
        interval = _TF_TO_YAHOO.get(tf)
        if interval is None:
            raise VenueError(400, f"unsupported timeframe {timeframe!r} "
                                  "for yahoo bars")
        bar_ms = _TF_MS[tf]
        need_days = max(1, math.ceil(limit * bar_ms / 86_400_000))
        # Yahoo intraday lookback cap is 60 days (15m/30m/60m); pick the
        # smallest range that plausibly covers `limit` SESSION bars.
        if need_days <= 4:
            rng = "5d"
        elif need_days <= 12:
            rng = "1mo"
        else:
            rng = "2mo"
        params: dict[str, Any] = {"interval": interval, "range": rng,
                                  "includePrePost": "false"}
        if end_ms:
            params["period2"] = int(end_ms / 1000.0)
        url = (f"{YAHOO_CHART_URL}/{urllib.parse.quote(clean_symbol(symbol))}"
               f"?{urllib.parse.urlencode(params)}")
        status, payload = self._send("GET", url, None, {
            "User-Agent": "kaos-multibot/1.0"})
        if status != 200 or not isinstance(payload, dict):
            self._remember_error(f"yahoo bars {symbol}: status={status}")
            raise VenueError(status or None, f"yahoo bars failed "
                            f"({status or 'network'})")
        try:
            result = payload["chart"]["result"][0]
            ts = result.get("timestamp") or []
            quote = (result["indicators"]["quote"] or [{}])[0]
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            vols = quote.get("volume") or []
        except (KeyError, IndexError, TypeError):
            self._remember_error(f"yahoo bars {symbol}: malformed payload")
            raise VenueError(None, "yahoo bars malformed payload") from None
        out: list[list] = []
        for i, sec in enumerate(ts):
            o, hi, lo, c, v = (opens[i] if i < len(opens) else None,
                               highs[i] if i < len(highs) else None,
                               lows[i] if i < len(lows) else None,
                               closes[i] if i < len(closes) else None,
                               vols[i] if i < len(vols) else None)
            if None in (o, hi, lo, c):
                continue
            out.append(self._kline_row(int(sec) * 1000, float(o), float(hi),
                                       float(lo), float(c), float(v or 0.0),
                                       tf))
        return out[-int(limit):]

    @staticmethod
    def _kline_row(open_ms: int, o: float, h: float, low: float, c: float,
                   v: float, timeframe: str) -> list:
        """One Binance-layout kline row (indices/types matched to the crypto
        feed: [0] int open ms, [1..5] floats, [6] int close ms, [7] quote
        vol, [8..11] Binance filler)."""
        bar_ms = _TF_MS.get(timeframe, 900_000)
        return [open_ms, o, h, low, c, v, open_ms + bar_ms - 1, c * v,
                0, 0.0, 0.0, "0.0"]
