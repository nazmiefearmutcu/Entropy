"""Persistent watchlist: an insertion-ordered JSON file of :class:`SymbolInfo` items.

Writes are atomic (tmp + ``os.replace``); a corrupt or missing file starts empty and
never crashes the app. In-memory state only mutates when the disk write succeeds, so
memory and disk can never diverge.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import msgspec

from entropy.data.universe import SymbolInfo

log = logging.getLogger(__name__)


class _StoredItem(msgspec.Struct):
    """On-disk item shape; ``venue`` is optional for forward/backward compatibility."""

    symbol: str
    asset_class: str = "equity"
    name: str = ""
    venue: str = ""


def _derive_venue(symbol: str) -> str:
    return symbol.split(":", 1)[0] if ":" in symbol else "us"


class Watchlist:
    """Deduped, insertion-ordered symbol list persisted to ``path``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._items: dict[str, SymbolInfo] = {}  # symbol -> info, insertion-ordered
        self._load()

    # --- queries ---------------------------------------------------------

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._items

    def items(self) -> list[SymbolInfo]:
        return list(self._items.values())

    # --- mutations (persisted atomically; disk failure leaves memory unchanged)

    def add(self, info: SymbolInfo) -> bool:
        """Append ``info``; returns False (no write) if the symbol is already present."""
        if info.symbol in self._items:
            return False
        self._items[info.symbol] = info
        try:
            self._save()
        except OSError:
            del self._items[info.symbol]
            raise
        return True

    def remove(self, symbol: str) -> bool:
        """Drop ``symbol``; returns False (no write) if it was not present."""
        info = self._items.pop(symbol, None)
        if info is None:
            return False
        try:
            self._save()
        except OSError:
            self._items[symbol] = info  # NOTE: loses original position on failure
            raise
        return True

    def toggle(self, info: SymbolInfo) -> bool:
        """Add if absent, remove if present; returns whether it is now present."""
        if info.symbol in self._items:
            self.remove(info.symbol)
            return False
        self.add(info)
        return True

    # --- persistence -------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = self._path.read_bytes()
        except OSError:
            return  # missing file: start empty, no log (normal first run)
        try:
            stored = msgspec.json.decode(raw, type=list[_StoredItem])
        except msgspec.DecodeError:
            log.debug("watchlist: corrupt file at %s; starting empty", self._path)
            return
        for item in stored:
            if not item.symbol or item.symbol in self._items:
                continue
            self._items[item.symbol] = SymbolInfo(
                symbol=item.symbol,
                name=item.name,
                asset_class=item.asset_class,
                venue=item.venue or _derive_venue(item.symbol),
            )

    def _save(self) -> None:
        payload = [
            {"symbol": i.symbol, "asset_class": i.asset_class, "name": i.name, "venue": i.venue}
            for i in self._items.values()
        ]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            tmp.write_bytes(msgspec.json.encode(payload))
            os.replace(tmp, self._path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
