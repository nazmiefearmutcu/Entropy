# src/entropy/venues/base.py
"""Venue abstraction for the KAOS multibot: protocols formalizing the
duck-typed executor surface the live runner already speaks.

ZERO-CRYPTO-CHANGE CONTRACT
---------------------------
The crypto production path (``scripts/entropy_live_paper.py`` +
``scripts/kaos_testnet_exec.py``) talks to its executor through
``getattr(self.exec_, "<name>", None)`` feature-detections and direct calls.
Every method name below is copied VERBATIM from that surface — these names are
load-bearing and MUST NOT be renamed (survey-entropy.md §F-3 / trap #4):

    place_market_order(symbol, side, qty, reduce_only=False) -> dict
    place_venue_stop(sym, pos_side, stop_price)              -> str | None
    place_venue_tp(sym, pos_side, qty, tp_price)             -> str | None
    cancel_venue_stop(sym)                                   -> bool
    cancel_stop_by_id(sym, order_id)                         -> bool
    cancel_stale_venue_stops(symbols=None)                   -> int
    cancel_venue_tp(sym)                                     -> bool
    adopt_open_stop(sym, pos_side="long")                    -> str | None
    adopt_open_tp(sym, pos_side)                             -> str | None
    algo_order_alive(sym, algoid)                            -> bool
    order_alive(sym, order_id)                               -> bool
    sweep_stale_stops(sym, pos_side)                         -> int
    get_balance()                                            -> dict
    get_open_positions()                                     -> list[dict]
    get_price(symbol)                                        -> float
    get_funding_rate(symbol)                                 -> dict | None
    banned_until_ms()                                        -> int
    is_banned(now_ms=None)                                   -> bool
    is_market_open(now_ms)                                   -> bool      [NEW]
    attributes: venue_id [NEW], host, sizing_pct, leverage, slots, last_errors

Naming note for reviewers cross-checking the campaign contract: the contract
prose shorthand ``cancel_order`` / ``cancel_stale_stops`` / ``adopt_open_stops``
/ ``stop_alive`` / ``tp_alive`` maps to the REAL method names
``cancel_venue_stop``+``cancel_stop_by_id`` / ``cancel_stale_venue_stops`` /
``adopt_open_stop`` / ``algo_order_alive`` / ``order_alive`` as they exist in
``scripts/kaos_testnet_exec.py`` (lines verified 2026-09-09). The code is the
source of truth; nothing was renamed.

Shorthand results (fail-open contract, identical envelope on every venue):
``place_market_order`` returns ``{"ok": True, "order_id": ..., "status": ...,
"avg_price": ..., "qty_used": ..., "bumped": ...}`` and, when the order was
deliberately NOT sent, additionally ``"skipped": <reason>`` (margin /
notional_cap / dust / already_closed / pdt / min_notional). Runner-side error
handlers catch broad ``Exception`` so :class:`VenueError` mirrors
``kaos_testnet_exec.ExecutorError`` (``code``/``msg`` attributes, key/IP-safe
messages).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class VenueError(Exception):
    """Venue HTTP/protocol error with a machine-readable ``code``.

    Mirrors ``kaos_testnet_exec.ExecutorError``: ``code`` may be ``None`` for
    network-level failures; ``msg`` never contains raw API keys or IPs.
    """

    def __init__(self, code: Any, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg or ""


class VenueUnavailable(VenueError):
    """Raised by an INERT executor (e.g. Alpaca adapter booted without keys).

    Honesty contract: a venue without credentials never silently pretends to
    trade — every order method raises; ``available`` is ``False``; bars stay
    reachable via the keyless tier.
    """


@dataclass(slots=True)
class OrderResult:
    """Canonical shape of the dict returned by ``place_market_order``.

    The adapters return PLAIN DICTS (the runner consumes dicts and the crypto
    envelope is load-bearing); this dataclass documents the required keys and
    can build one via :meth:`as_dict`.
    """

    ok: bool = True
    order_id: str | None = None
    status: str = "?"
    avg_price: float | None = None
    qty_used: float = 0.0
    bumped: bool = False
    skipped: str | None = field(default=None)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "order_id": self.order_id,
            "status": self.status,
            "avg_price": self.avg_price,
            "qty_used": float(self.qty_used),
            "bumped": self.bumped,
        }
        if self.skipped is not None:
            out["skipped"] = self.skipped
        return out


@runtime_checkable
class VenueClock(Protocol):
    """Market-hours abstraction.

    ``session_label`` returns one of "RTH" | "PRE" | "POST" | "CLOSED" for
    session venues, or "24/7" for always-open crypto venues.
    """

    def is_open(self, now_ms: int) -> bool: ...

    def session_label(self, now_ms: int) -> str: ...


@runtime_checkable
class VenueAdapter(Protocol):
    """The load-bearing executor surface, formalized.

    Implementations must keep method names AND positional signatures identical
    to ``TestnetExecutor`` (the runner feature-detects each name via getattr).
    ``venue_id`` and ``is_market_open`` are protocol additions; existing crypto
    behavior is untouched because the crypto path never calls them.
    """

    venue_id: str
    host: str
    sizing_pct: float
    leverage: float
    slots: int

    # ---- market hours (NEW; crypto venue answers True unconditionally) ----
    def is_market_open(self, now_ms: int) -> bool: ...

    # ---- orders -----------------------------------------------------------
    def place_market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> dict: ...

    def place_venue_stop(self, sym: str, pos_side: str,
                         stop_price: float) -> str | None: ...

    def place_venue_tp(self, sym: str, pos_side: str, qty: float,
                       tp_price: float) -> str | None: ...

    # ---- bracket bookkeeping ----------------------------------------------
    def cancel_venue_stop(self, sym: str) -> bool: ...

    def cancel_stop_by_id(self, sym: str, order_id: str) -> bool: ...

    def cancel_stale_venue_stops(self, symbols: Any = None) -> int: ...

    def cancel_venue_tp(self, sym: str) -> bool: ...

    def adopt_open_stop(self, sym: str, pos_side: str = "long") -> str | None: ...

    def adopt_open_tp(self, sym: str, pos_side: str) -> str | None: ...

    def algo_order_alive(self, sym: str, algoid: str) -> bool: ...

    def order_alive(self, sym: str, order_id: str) -> bool: ...

    def sweep_stale_stops(self, sym: str, pos_side: str) -> int: ...

    # ---- reads -------------------------------------------------------------
    def get_balance(self) -> dict: ...

    def get_open_positions(self) -> list[dict]: ...

    def get_price(self, symbol: str) -> float: ...

    def get_funding_rate(self, symbol: str) -> dict | None: ...

    # ---- ban state (crypto IP-ban contract; session venues answer 0/False)
    def banned_until_ms(self) -> int: ...

    def is_banned(self, now_ms: float | None = None) -> bool: ...
