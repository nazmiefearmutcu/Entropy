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

    def __init__(self, run_dir: str, mode: str = "paper") -> None:
        os.makedirs(run_dir, exist_ok=True)
        self.run_dir = run_dir
        self.mode = mode  # "paper" | "live" — stamped on every record so runs are never confused
        self._events = os.path.join(run_dir, "events.jsonl")
        self._fills = os.path.join(run_dir, "fills.csv")
        self._equity = os.path.join(run_dir, "equity.csv")
        self._init_csv(self._fills, _FILL_HEADER)
        self._init_csv(self._equity, _EQUITY_HEADER)
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
