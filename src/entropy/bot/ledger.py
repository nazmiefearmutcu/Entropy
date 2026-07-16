from __future__ import annotations

import csv
import json
import os
from typing import Any

from .orders import Fill, OrderIntent
from .portfolio import PortfolioSnapshot

_FILL_HEADER = ["ts_ns", "order_id", "symbol", "side", "intent", "qty", "price", "fee", "slippage"]
_EQUITY_HEADER = [
    "ts_ns", "equity", "cash", "realized_pnl", "unrealized_pnl", "daily_pnl", "open_count"
]


class Ledger:
    """Append-only trade journal: structured events to events.jsonl, plus flat CSVs for
    fills and the equity curve. All writes are synchronous appends (call off the hot path
    for equity; fills are rare)."""

    def __init__(self, run_dir: str, mode: str = "paper", trade_csv_path: str = "entropy_trades.csv") -> None:
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
    """Initialize the trade CSV file with headers if it does not exist."""
    if not path:
        return
    log_dir = os.path.dirname(path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(["Symbol", "Side", "Open Price", "Close Price"])


def record_trade_open(csv_path: str, symbol: str, side: str, price: float) -> None:
    """Record an opened trade in the trade CSV file."""
    if not csv_path:
        return
    init_trade_csv(csv_path)
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([symbol, side, str(price), ""])
    except Exception:
        pass


def record_trade_close(csv_path: str, symbol: str, side: str, price: float) -> None:
    """Record a closed trade in the trade CSV file by filling the last matching open trade's close price."""
    if not csv_path or not os.path.exists(csv_path):
        return
    try:
        rows: list[list[str]] = []
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is not None:
                rows.append(header)
            for row in reader:
                rows.append(row)
        
        filled = False
        for idx in range(len(rows) - 1, 0, -1):
            row = rows[idx]
            if (
                len(row) >= 4
                and row[0].upper() == symbol.upper()
                and row[1].upper() == side.upper()
                and not row[3]
            ):
                row[3] = str(price)
                filled = True
                break
                
        if filled:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
    except Exception:
        pass
