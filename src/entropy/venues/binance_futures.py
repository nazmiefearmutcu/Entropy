# src/entropy/venues/binance_futures.py
"""BinanceFuturesAdapter — THIN delegation wrapper over the injected executor.

The crypto production executor (``TestnetExecutor`` in
``scripts/kaos_testnet_exec.py``) IS the venue; this adapter adds nothing but
the two protocol additions the multibot scheduler needs (``venue_id`` and
``is_market_open``) and a 24/7 clock. It deliberately does NOT import the
scripts module (not a package module — Wave-2 supervisor injects the
instance); there are ZERO logic moves here: every method is pure delegation,
so wrapping the real executor is behaviorally identical to using it directly.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from entropy.venues.base import VenueError  # noqa: F401  (re-export contract)
from entropy.venues.clock import CryptoClock


class BinanceFuturesAdapter:
    """VenueAdapter view of a Binance USD-M futures executor instance.

    Constructor takes the ALREADY-BUILT executor object (e.g. the product of
    ``kaos_testnet_exec.make_executor_from_env()`` built by the supervisor).
    All attributes/methods delegate 1:1.
    """

    venue_id = "binance-futures"

    def __init__(self, executor: Any, clock: CryptoClock | None = None) -> None:
        if executor is None:
            raise ValueError(
                "BinanceFuturesAdapter requires an injected executor instance "
                "(build via kaos_testnet_exec.make_executor_from_env(); the "
                "venues package must not import scripts/)")
        self._exec = executor
        self.clock = clock if clock is not None else CryptoClock()

    # ---- protocol additions ------------------------------------------------
    def is_market_open(self, now_ms: int) -> bool:
        """Crypto venue: 24/7 — delegates to the clock (always True)."""
        return bool(self.clock.is_open(now_ms))

    def session_label(self, now_ms: int) -> str:
        return str(self.clock.session_label(now_ms))

    # ---- delegated attributes ----------------------------------------------
    @property
    def host(self) -> str:
        return str(getattr(self._exec, "host", ""))

    @property
    def sizing_pct(self) -> float:
        return float(getattr(self._exec, "sizing_pct", 0.0) or 0.0)

    @property
    def leverage(self) -> float:
        return float(getattr(self._exec, "leverage", 1) or 1)

    @property
    def slots(self) -> int:
        return int(getattr(self._exec, "slots", 1) or 1)

    @property
    def last_errors(self) -> deque:
        return getattr(self._exec, "last_errors", deque(maxlen=0))

    # ---- orders (pure delegation, names/signatures verbatim) ----------------
    def place_market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> dict:
        return self._exec.place_market_order(symbol, side, qty,
                                             reduce_only=reduce_only)

    def place_venue_stop(self, sym: str, pos_side: str,
                         stop_price: float) -> str | None:
        return self._exec.place_venue_stop(sym, pos_side, stop_price)

    def place_venue_tp(self, sym: str, pos_side: str, qty: float,
                       tp_price: float) -> str | None:
        return self._exec.place_venue_tp(sym, pos_side, qty, tp_price)

    # ---- bracket bookkeeping -------------------------------------------------
    def cancel_venue_stop(self, sym: str) -> bool:
        return self._exec.cancel_venue_stop(sym)

    def cancel_stop_by_id(self, sym: str, order_id: str) -> bool:
        return self._exec.cancel_stop_by_id(sym, order_id)

    def cancel_stale_venue_stops(self, symbols: Any = None) -> int:
        return self._exec.cancel_stale_venue_stops(symbols)

    def cancel_venue_tp(self, sym: str) -> bool:
        return self._exec.cancel_venue_tp(sym)

    def adopt_open_stop(self, sym: str, pos_side: str = "long") -> str | None:
        return self._exec.adopt_open_stop(sym, pos_side)

    def adopt_open_tp(self, sym: str, pos_side: str) -> str | None:
        return self._exec.adopt_open_tp(sym, pos_side)

    def algo_order_alive(self, sym: str, algoid: str) -> bool:
        return self._exec.algo_order_alive(sym, algoid)

    def order_alive(self, sym: str, order_id: str) -> bool:
        return self._exec.order_alive(sym, order_id)

    def sweep_stale_stops(self, sym: str, pos_side: str) -> int:
        return self._exec.sweep_stale_stops(sym, pos_side)

    # ---- reads ----------------------------------------------------------------
    def get_balance(self) -> dict:
        return self._exec.get_balance()

    def get_open_positions(self) -> list[dict]:
        return self._exec.get_open_positions()

    def get_price(self, symbol: str) -> float:
        return self._exec.get_price(symbol)

    def get_funding_rate(self, symbol: str) -> dict | None:
        """Funding passthrough — crypto perp concept, real rates here."""
        return self._exec.get_funding_rate(symbol)

    # ---- ban state --------------------------------------------------------------
    def banned_until_ms(self) -> int:
        return int(getattr(self._exec, "banned_until_ms", lambda: 0)() or 0)

    def is_banned(self, now_ms: float | None = None) -> bool:
        fn = getattr(self._exec, "is_banned", None)
        if fn is None:
            return False
        return bool(fn(now_ms) if now_ms is not None else fn())
