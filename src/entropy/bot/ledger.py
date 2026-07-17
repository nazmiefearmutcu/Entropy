from __future__ import annotations

import csv
import json
import logging
import os
import time
from typing import Any

from .orders import Fill, OrderIntent
from .portfolio import PortfolioSnapshot

_FILL_HEADER = ["ts_ns", "order_id", "symbol", "side", "intent", "qty", "price", "fee", "slippage"]
_EQUITY_HEADER = [
    "ts_ns", "equity", "cash", "realized_pnl", "unrealized_pnl", "daily_pnl", "open_count"
]
# Append-only trade journal: OPEN rows leave close_price empty; CLOSE rows carry both the
# entry price of the position being closed and the exit price. `ts` is unix epoch seconds.
_TRADE_HEADER = ["ts", "symbol", "side", "event", "open_price", "close_price"]

_log = logging.getLogger(__name__)

# One-time flag: the first failed trade-CSV write is logged at DEBUG (no handlers are
# configured, so anything louder would land on stderr over the TUI); later failures are
# silent to avoid spamming from a persistently broken path/disk.
_write_failure_logged = False

# In-memory registry of open positions per trade-CSV path:
# (SYMBOL, SIDE) -> stack of entry prices, matched LIFO (most recent open first, the same
# order the old rewrite logic used). record_trade_close pairs entry and exit prices from
# here, so a close is a pure append — the CSV is never re-read or rewritten.
_open_positions: dict[str, dict[tuple[str, str], list[float]]] = {}


def _note_write_failure(exc: OSError) -> None:
    global _write_failure_logged
    if not _write_failure_logged:
        _write_failure_logged = True
        _log.debug("trade CSV write failed (further failures suppressed): %s", exc)


class Ledger:
    """Append-only trade journal: structured events to events.jsonl, plus flat CSVs for
    fills and the equity curve. All writes are synchronous appends (call off the hot path
    for equity; fills are rare)."""

    def __init__(
        self, run_dir: str, mode: str = "paper", trade_csv_path: str = "entropy_trades.csv"
    ) -> None:
        os.makedirs(run_dir, exist_ok=True)
        self.run_dir = run_dir
        self.mode = mode  # "paper" | "live" — stamped on every record so runs are never confused
        self.trade_csv_path = trade_csv_path
        self._events = os.path.join(run_dir, "events.jsonl")
        self._fills = os.path.join(run_dir, "fills.csv")
        self._equity = os.path.join(run_dir, "equity.csv")
        self._init_csv(self._fills, _FILL_HEADER)
        self._init_csv(self._equity, _EQUITY_HEADER)
        init_trade_csv(self.trade_csv_path)
        self._write_meta()

    def _write_meta(self) -> None:
        meta_path = os.path.join(self.run_dir, "meta.json")
        if not os.path.exists(meta_path):
            with open(meta_path, "w") as fh:
                json.dump({"mode": self.mode}, fh)

    @staticmethod
    def _init_csv(path: str, header: list[str]) -> None:
        if not os.path.exists(path):
            with open(path, "w", newline="") as fh:
                csv.writer(fh).writerow(header)

    def record_event(self, kind: str, payload: dict[str, Any]) -> None:
        with open(self._events, "a") as fh:
            fh.write(json.dumps({"kind": kind, "mode": self.mode, **payload}) + "\n")

    def record_fill(self, fill: Fill, intent: OrderIntent) -> None:
        with open(self._fills, "a", newline="") as fh:
            csv.writer(fh).writerow([
                fill.ts_ns, fill.order_id, fill.symbol, fill.side.value, intent.value,
                fill.qty, fill.price, fill.fee, fill.slippage,
            ])
        self.record_event("fill", {
            "ts_ns": fill.ts_ns, "symbol": fill.symbol, "side": fill.side.value,
            "intent": intent.value, "qty": fill.qty, "price": fill.price, "fee": fill.fee,
        })

    def record_equity(self, snap: PortfolioSnapshot) -> None:
        with open(self._equity, "a", newline="") as fh:
            csv.writer(fh).writerow([
                snap.ts_ns, snap.equity, snap.cash, snap.realized_pnl,
                snap.unrealized_pnl, snap.daily_pnl, snap.open_count,
            ])

    def record_reject(self, symbol: str, reason: str) -> None:
        self.record_event("reject", {"symbol": symbol, "reason": reason})

    def record_risk_change(self, old: str, new: str) -> None:
        self.record_event("risk_profile_changed", {"from": old, "to": new})

    def record_trade_open(self, symbol: str, side: str, price: float) -> None:
        record_trade_open(self.trade_csv_path, symbol, side, price)

    def record_trade_close(self, symbol: str, side: str, price: float) -> None:
        record_trade_close(self.trade_csv_path, symbol, side, price)


def init_trade_csv(path: str) -> None:
    """Create the append-only trade CSV with its header if needed.

    A file bearing a foreign header — notably the legacy rewrite format
    ("Symbol,Side,Open Price,Close Price") — is renamed to ``<name>.bak-<unix_ts>`` and a
    fresh file is started: the legacy format filled close prices by rewriting rows in
    place, so appending new-format rows to it would corrupt both formats. No migration is
    attempted; the .bak file keeps the old data intact.
    """
    if not path:
        return
    try:
        log_dir = os.path.dirname(path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        if os.path.exists(path):
            try:
                with open(path, newline="", encoding="utf-8") as fh:
                    first = next(csv.reader(fh), None)
            except (UnicodeDecodeError, csv.Error):
                first = []  # not CSV text at all -> foreign file, set it aside below
            if first == _TRADE_HEADER:
                return
            if first is not None:
                os.replace(path, f"{path}.bak-{int(time.time())}")
        # Fresh file (brand-new or legacy set aside): stale registry entries from a
        # previous file at this path would pair a future CLOSE with an entry price
        # whose OPEN row is not in this file — drop them with the old file.
        _open_positions.pop(path, None)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(_TRADE_HEADER)
    except OSError as exc:
        _note_write_failure(exc)


def record_trade_open(csv_path: str, symbol: str, side: str, price: float) -> None:
    """Append an OPEN row and remember the entry price for the matching close."""
    if not csv_path:
        return
    # Ensure the file exists FIRST (a fresh file clears any stale registry entries),
    # then track the position — even if the disk write below fails: the position is
    # real either way, and the eventual CLOSE row can then still carry the correct
    # entry price. init_trade_csv swallows OSError itself.
    init_trade_csv(csv_path)
    _open_positions.setdefault(csv_path, {}).setdefault(
        (symbol.upper(), side.upper()), []
    ).append(price)
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([f"{time.time():.3f}", symbol, side, "OPEN", str(price), ""])
    except OSError as exc:
        _note_write_failure(exc)


def record_trade_close(csv_path: str, symbol: str, side: str, price: float) -> None:
    """Append a CLOSE row carrying both the entry and the exit price.

    The entry price comes from the in-memory open-position registry (LIFO per
    symbol+side, case-insensitive — the same matching the old rewrite logic used); the
    file is never re-read. A close with no known open (e.g. state lost across a restart)
    still appends a CLOSE row with an empty open_price instead of dropping the event.
    """
    if not csv_path:
        return
    init_trade_csv(csv_path)  # a fresh file clears stale registry entries first
    stack = _open_positions.get(csv_path, {}).get((symbol.upper(), side.upper()))
    open_price = str(stack[-1]) if stack else ""  # peek: pop only after the write lands
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                [f"{time.time():.3f}", symbol, side, "CLOSE", open_price, str(price)]
            )
    except OSError as exc:
        # The CLOSE row never hit the disk: keep the entry on the stack so a
        # retried close still pairs with the correct open price.
        _note_write_failure(exc)
    else:
        if stack:
            stack.pop()
