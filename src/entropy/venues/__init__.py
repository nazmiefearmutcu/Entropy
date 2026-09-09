# src/entropy/venues/__init__.py
"""KAOS multibot venue abstraction.

Formalizes the duck-typed executor surface the live runner already speaks
(``entropy.venues.base.VenueAdapter`` — method names copied VERBATIM from
``scripts/kaos_testnet_exec.py``; names are load-bearing, never renamed) and
provides two adapters:

- :class:`BinanceFuturesAdapter` — THIN pure-delegation wrapper around an
  INJECTED crypto executor instance (this package never imports
  ``scripts/``; the Wave-2 supervisor builds ``TestnetExecutor`` via
  ``make_executor_from_env()`` and hands the object in). Crypto behavior is
  bit-identical: the adapter adds only ``venue_id`` and ``is_market_open``.
- :class:`AlpacaEquitiesAdapter` — NASDAQ venue, stdlib-only HTTP, paper by
  default, honest PAPER/live labeling, PDT gate, RTH clock, kline-shaped
  bars with keyless Yahoo fallback.

Schedulers use :class:`CryptoClock` (24/7) and :class:`USEquitiesClock`
(RTH/PRE/POST/CLOSED via the crocodile USMarketCalendar).
"""
from __future__ import annotations

from entropy.venues.alpaca_equities import (
    AlpacaEquitiesAdapter,
    PdtCounter,
    clean_symbol,
)
from entropy.venues.base import (
    OrderResult,
    VenueAdapter,
    VenueClock,
    VenueError,
    VenueUnavailable,
)
from entropy.venues.binance_futures import BinanceFuturesAdapter
from entropy.venues.clock import CryptoClock, USEquitiesClock

__all__ = [
    "AlpacaEquitiesAdapter",
    "BinanceFuturesAdapter",
    "CryptoClock",
    "OrderResult",
    "PdtCounter",
    "USEquitiesClock",
    "VenueAdapter",
    "VenueClock",
    "VenueError",
    "VenueUnavailable",
    "clean_symbol",
]
