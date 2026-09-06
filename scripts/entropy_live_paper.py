#!/usr/bin/env python3
"""Shipped s20 consensus strategy: continuous LIVE PAPER trading on real
Binance 15m bars.

Usage:
  python scripts/entropy_live_paper.py [--symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT] [--cash 100] [--once]

Startup: builds the shipped s20 BotConfig (harness-identical defaults: consensus
trend votes, long-only, max_hold 192 bars, 20-sigma stop / 4-sigma TP, entry
grace 3, 10%/trade, max 4 concurrent, 40% exposure, Binance spot 10 bps fee +
3 bps slippage per side), fetches the last 300 closed 15m bars per symbol
(100 warmup + 200 evaluated) and feeds them through ONE BotRunner with shared
equity (warmup positions are liquidated at the first evaluated tick).

Then it loops FOREVER: ~15 s after every 15m bar close the newly closed bar is
fetched per symbol and fed as 4 ticks (O, L, H, C — direction-aware stop-first
ordering, exactly like scripts/entropy_accuracy_btc15m.py). Multi-bar gaps
(e.g. after downtime) are caught up in order. Binance fetch errors back off
exponentially (1 s → 2 → 4 … capped at 60 s; reset after a successful bar) and
an hourly "[heartbeat] up Xh Ym, equity $..., trades=N" line is logged. Stop
with Ctrl+C — a final state is written on exit.

State: after every fed bar AND at least once per 60 s the account state is
ATOMICALLY written (tmp + os.replace) to
  reports/live-paper/state.json
THAT FILE is the monitoring interface — watchers/monitors must read the state
file, not the process. Schema (exact):
  {"ts_utc", "mode": "PAPER_LIVE", "equity_usd", "day_pnl_usd", "trades_today",
   "positions": [{symbol, side, qty, entry_price, mark_price,
                  unrealized_pnl_usd}],
   "equity_history": [[epoch_ms, equity], ...]        (last 500),
   "closed_trades": [{symbol, side, entry_price, exit_price, pnl_usd,
                      exit_reason, exit_utc}, ...]    (last 40)}
Mark price = the symbol's last closed bar close; unrealized = (mark-entry)*qty
for longs, (entry-mark)*qty for shorts.

The main loop is crash-resistant: exceptions are caught, logged to stdout
(flushed) and the loop keeps running. Requires only the repo's existing
dependencies (urllib/json + src/entropy modules); no pip installs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import sys
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

# Reuse the harness verbatim: fetch_klines, build_ticks, BotRunner,
# _BarrierLedger, _liquidate_open_positions, utc_day, resolve_symbol.
_HARNESS = REPO / "scripts" / "entropy_accuracy_btc15m.py"
_spec = importlib.util.spec_from_file_location("entropy_accuracy_harness", _HARNESS)
ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ha)

from entropy.bot.config import BotConfig, ConsensusConfig, MarketCostConfig, RiskOverrides
from entropy.bot.portfolio import PositionSide

from kaos_testnet_exec import make_executor_from_env  # noqa: E402 (futures testnet mirror)

_NS = 1_000_000_000
BAR_MS = 15 * 60 * 1000
POST_CLOSE_DELAY_MS = 15_000     # feed a bar ~15 s after its close
POLL_S = 10.0                    # main loop cadence
STATE_HEARTBEAT_S = 60.0         # state rewrite even with no new bar
WARMUP_BARS = 100
EVAL_BARS = 200
MAX_CATCHUP_BARS = 2000          # cap backlog replay after downtime
EQUITY_HISTORY_MAX = 500
CLOSED_TRADES_MAX = 40
CLOSED_TRADES_KEEP = 400         # in-memory cap for closed_all (10x the
                                 # reported window; trades_today base absorbs
                                 # trimmed same-day trades -> counter stays exact)
FETCH_BACKOFF_START_S = 1.0      # Binance fetch error backoff: 1 -> 2 -> 4 ...
FETCH_BACKOFF_MAX_S = 60.0       # ... capped at 60 s (reset on success)
FETCH_MAX_ATTEMPTS = 8           # consecutive failures before raising; the
                                 # forever-loop's own retry keeps the bot alive
HEARTBEAT_S = 3600.0             # hourly "[heartbeat] ..." log cadence

DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"


def _f(x: float) -> float:
    """Sanitize a float for the state JSON (no NaN/Inf — a watcher must never
    see a non-finite number; one bad tick must not block all state writes)."""
    x = float(x)
    return x if math.isfinite(x) else 0.0


def _closed_key(t: dict[str, Any]) -> tuple:
    """Identity of a closed trade. exit_utc is nanosecond-derived, so two
    distinct fills can never collide; a collision means the SAME trade was
    regenerated (restart re-feeds recent bars) and must not be re-appended."""
    # 2-decimal rounding + second-precision timestamp: restart replays must
    # produce IDENTICAL keys even when float last-digits differ by 1 ulp.
    return (t["symbol"], t["side"],
            round(float(t["entry_price"]), 2), round(float(t["exit_price"]), 2),
            round(float(t["pnl_usd"]), 2), t["exit_reason"],
            str(t["exit_utc"]).split(".")[0])


def load_previous_state(path: Path) -> dict[str, Any] | None:
    """Read the previous state.json for restart persistence.

    Returns the parsed dict, or None when the file is absent. A CORRUPT file
    (JSON parse error / not a state object) is backed up to '<path>.broken'
    once and we start from scratch — a broken monitor file must never kill
    the bot."""
    if not path.exists():
        return None
    st: Any = None
    reason = ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            st = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        st, reason = None, f"JSON parse error: {e}"
    except OSError as e:
        print(f"[live-paper] WARNING: {path} unreadable ({e}) — starting fresh",
              flush=True)
        return None
    if not isinstance(st, dict):
        if not reason:
            reason = f"not a state object (got {type(st).__name__})"
        broken = path.with_name(path.name + ".broken")
        try:
            shutil.copy2(path, broken)
            print(f"[live-paper] WARNING: {path} corrupt ({reason}); backed up "
                  f"to {broken} — starting fresh", flush=True)
        except Exception as e:
            print(f"[live-paper] WARNING: {path} corrupt ({reason}); backup "
                  f"failed ({e}) — starting fresh", flush=True)
        return None
    return st


def build_cfg(raws: list[str], cash: float, out_dir: Path) -> BotConfig:
    """Shipped s20 config — values are the harness defaults VERBATIM
    (scripts/entropy_accuracy_btc15m.py main(), the Round-2 winner)."""
    symbols = tuple(ha.resolve_symbol(r) for r in raws)
    return BotConfig(
        mode="paper",
        starting_cash=cash,
        strategies=("consensus",),
        symbols=symbols,
        ema_symbol=symbols[0],
        ema_fast=9,
        ema_slow=21,
        momentum_min_pct=0.15,
        timeframe="15m",
        bar_s=900.0,
        warmup=False,
        market_costs=MarketCostConfig(),
        cost_aware=True,
        cost_edge_mult=1.0,
        consensus=ConsensusConfig(
            threshold=0.5,
            exit_mode="trail",
            min_hold_bars=5,
            cooldown_bars=4,
            move_floor=0.0003,
            vote_mode="trend",
            normalize="total",
            min_participation=0.5,
            direction_bars=20,
            confirm_bars=2,
            trail_pct=0.3,
            max_hold_bars=192,
            long_only=True,
        ),
        risk_overrides=RiskOverrides(
            per_trade_pct=10.0,
            max_concurrent=4,
            stop_loss_pct=1.5,
            take_profit_pct=1.2,
            max_total_exposure_pct=40.0,
            max_daily_loss_pct=40.0,
            cooldown_s=180.0,
            min_volatility_pct=0.05,
            vol_window_s=900.0,
            stop_mode="sigma",
            stop_sigma_mult=20.0,
            tp_sigma_mult=4.0,
            risk_trail_pct=0.0,
            entry_grace_bars=3,
        ),
        console_log_path=str(out_dir / "console.log"),
        trade_csv_path=str(out_dir / "trades.csv"),
    )


class LivePaper:
    """One BotRunner (shared equity across symbols) driven bar-by-bar forever."""

    def __init__(self, raws: list[str], cash: float, out_dir: Path) -> None:
        self.raws = raws
        self.order = sorted(raws)  # deterministic cross-symbol feed order
        self.syms = {r: ha.resolve_symbol(r) for r in raws}
        self.cfg = build_cfg(raws, cash, out_dir)
        self.runner = ha.BotRunner(self.cfg, run_dir=str(out_dir / "ledger"))
        self.ledger = ha._BarrierLedger(self.runner)
        self.runner.ledger = self.ledger  # type: ignore[assignment]
        # Per-symbol close-side cost schedule for barrier-level fill clamping
        # (same fallbacks as the harness pairing step). s20 ships
        # risk_trail_pct=0.0, so the stop is never ratcheted and the
        # open-captured barrier levels are authoritative for every exit.
        self._costs: dict[str, tuple[float, float]] = {}
        for raw in raws:
            sym = self.syms[raw]
            resolved = self.runner.executor.cost_model.for_symbol(sym)
            fee_bps = resolved.fee_bps if resolved.fee_bps is not None else self.cfg.fee_bps
            slip_bps = resolved.slippage_bps if resolved.slippage_bps is not None else self.cfg.slippage_bps
            self._costs[sym] = (float(fee_bps), float(slip_bps))
        self.cursor = 0                 # index into ledger.fills (paired trades)
        self.open_fills: dict[str, tuple[int, Any]] = {}
        self.closed_all: list[dict[str, Any]] = []
        self._closed_keys: set[tuple] = set()   # identity of every closed trade
        self.equity_history: list[list[float]] = []
        self.last_close: dict[str, float] = {}   # raw -> last closed bar close
        self.last_fed_open_ms = 0
        self._fetch_backoff_s = FETCH_BACKOFF_START_S
        self._prev_day: str | None = None
        # trades_today = base (persisted trades that fell out of the 40-trade
        # window) + trades still visible in closed_all; reset on UTC day change.
        self._trades_today_base = 0
        self._trades_today_day: str | None = None
        self.state_path = out_dir / "state.json"
        # Canli-donem ayristirmasi (denetim notu, 2026-09-06):
        # replay donemi (warmup+history beslemesi) egriye dahil EDILMEZ;
        # live_base_equity = canli donemin gercek baslangic sermayesi.
        self.live_base_equity: float | None = None
        self.equity_note: str | None = (
            "Canli donem 2026-09-05 20:39 UTC (temiz restart) itibarindadir; "
            "oncesi warmup/replay donemidir ve denetimde bir kismi dogrulanamadi.")
        # Run control (gateway AYARLAR writes this file; bot polls it):
        #   schedule.json = {"run_mode":"active|paused|scheduled",
        #                    "utc_start":H,"utc_end":H,"updated_utc":...}
        # paused / outside-window -> NEW entries rejected (exits always pass).
        self.schedule_path = out_dir / "schedule.json"
        self._schedule_mtime = 0.0
        self._schedule = {"run_mode": "active", "utc_start": None, "utc_end": None}
        # ---- futures testnet aynalama katmanı (fail-open) -----------------
        # KAOS_EXCHANGE_TESTNET=1 + BINANCE_API_KEY/SECRET env ile açılır;
        # kapalıyken (exec_ is None) davranış bit-özdeş kalır. Yalnız CANLI
        # döngüde (_mirror_live=True, start() sonunda set edilir) aynalanır —
        # startup replay'i asla aynalanmaz.
        self.exec_ = make_executor_from_env()
        self._mirror_live = False
        self.open_mirror: dict[str, dict[str, Any]] = {}
        self.exec_stats: dict[str, Any] = {"orders_sent": 0, "orders_failed": 0,
                                           "last_ok_utc": None, "last_error": None}
        self._exec_balance: dict | None = None
        self._last_balance_refresh: float | None = None
        # yetim-reconcile: mirror kapanış emri başarısız olan testnet
        # pozisyonları paper tarafı kapandıktan sonra asılı kalıyordu
        # (2026-09-06: XRP/BNB yetimleri). Periyodik düzeltme:
        self._last_reconcile: float = 0.0
        self.RECONCILE_EVERY_S = 900.0
        orig_evaluate = self.runner.risk.evaluate
        def _gated_evaluate(signal, portfolio, mark_px, ts_ns):
            from entropy.bot.signals import SignalAction as _SA
            from entropy.bot.risk.manager import RiskDecision as _RD
            if signal.action is not _SA.EXIT:
                eff = self._effective_run_mode()
                if eff != "paper":
                    return _RD(False, None, "run-control: " + eff)
            return orig_evaluate(signal, portfolio, mark_px, ts_ns)
        self.runner.risk.evaluate = _gated_evaluate  # type: ignore[assignment]

    # ---- tick feeding (harness _feed logic, one bar at a time) -------------
    def _on(self, t: dict[str, Any]) -> None:
        day = ha.utc_day(t["ts_ns"])
        if day != self._prev_day:
            # The runner's wall-clock rollover only fires in its async loops
            # (never started here), so keep the daily kill switch genuinely
            # daily the same way the harness does.
            if self._prev_day is not None:
                self.runner.portfolio.reset_day()
                self.runner.risk.reset_day()
            self.runner._utc_day = day
            self._prev_day = day
        self.runner.on_trade(t["symbol"], t["price"], t["amount"], t["side"], t["ts_ns"])

    def _feed_bar(self, by_open: dict[str, list[list[Any]]], bar_open_ms: int) -> None:
        """Feed one 15m bar for every symbol that has it. Ticks are re-stamped
        into per-symbol slots inside the bar (symbol j gets j*spacing + 0..3 s)
        so global time stays monotonic across symbols (multi-symbol harness
        convention). Direction-aware stop-first L/H ordering per symbol."""
        if bar_open_ms <= self.last_fed_open_ms:
            # Same-bar double-feed guard (process lifetime): a bar at or
            # before the last fed bar must never be re-fed. poll_bars already
            # filters (last_fed, target]; this is the belt-and-braces check.
            # On restart last_fed_open_ms starts at 0, so the intended
            # recent-bar replay still runs.
            return
        spacing = min(10, max(4, 880 // max(1, len(self.order))))
        for j, raw in enumerate(self.order):
            kl = by_open.get(raw, {}).get(bar_open_ms)
            if kl is None:
                continue
            sym = self.syms[raw]
            ticks = ha.build_ticks([kl], symbol=sym)  # O, L, H, C
            s = j * spacing
            bar_open_ns = (int(kl[0]) // BAR_MS) * BAR_MS * 1_000_000
            for t in ticks:
                off_s = (t["ts_ns"] // _NS) % 4
                t["ts_ns"] = bar_open_ns + (s + off_s) * _NS
            self._on(ticks[0])
            pos = self.runner.portfolio.positions.get(sym)
            rest = ticks[1:]
            if pos is not None and pos.side is PositionSide.SHORT and len(rest) == 3:
                # short: HIGH (stop side) before LOW (TP side), re-stamped
                h, low = dict(ticks[2]), dict(ticks[1])
                h["ts_ns"] = ticks[0]["ts_ns"] + _NS
                low["ts_ns"] = ticks[0]["ts_ns"] + 2 * _NS
                rest = [h, low, ticks[3]]
            for t in rest:
                self._on(t)
            self.last_close[raw] = float(kl[4])

    def _bars_by_open(self, klines: list[list[Any]]) -> dict[int, list[Any]]:
        # open_ms -> the kline row itself (build_ticks takes a list of rows)
        return {int(k[0]): k for k in klines}

    def _fetch_with_backoff(self, n: int, now_ms: int,
                            raw: str) -> list[list[Any]]:
        """ha.fetch_klines with exponential backoff on ANY fetch error:
        1 s -> 2 -> 4 -> ... capped at FETCH_BACKOFF_MAX_S (60 s). The backoff
        resets to the start value after every successful fetch. Every failed
        attempt is logged. After FETCH_MAX_ATTEMPTS consecutive failures the
        last error is raised — the forever-loop's own try/except retries on
        the next poll (backoff state persists), so a Binance outage slows the
        bot instead of killing it."""
        attempt = 0
        while True:
            attempt += 1
            try:
                ks = ha.fetch_klines(n, now_ms, None, raw=raw)
                self._fetch_backoff_s = FETCH_BACKOFF_START_S  # success: reset
                return ks
            except Exception as e:
                delay = self._fetch_backoff_s
                print(f"[live-paper] fetch {raw} attempt {attempt}/"
                      f"{FETCH_MAX_ATTEMPTS} failed ({type(e).__name__}: {e})"
                      f" — backoff {delay:.0f}s", flush=True)
                if attempt >= FETCH_MAX_ATTEMPTS:
                    raise
                time.sleep(delay)
                self._fetch_backoff_s = min(delay * 2.0, FETCH_BACKOFF_MAX_S)

    def _feed_history(self, klines: list[list[Any]], record: bool) -> None:
        by_open = self._bars_by_open(klines)
        for open_ms in sorted(by_open):
            self._feed_bar(by_open, open_ms)
            self.collect_closed()
            if record:
                eq = self.runner.portfolio.snapshot(
                    (open_ms + BAR_MS - 1) * 1_000_000).equity
                self._append_equity(open_ms + BAR_MS, eq)

    # ---- futures testnet mirroring (fail-open) -----------------------------
    def _mirror(self, symbol: str, side: str, qty: float,
                reduce_only: bool = False) -> dict | None:
        """Canlı paper dolgusunu futures testnet'te GERÇEK market emri olarak
        tekrarla. Hata asla runner'a sıçramaz (paper muhasebe doğru kalır);
        başarısızlık exec_stats'a yazılır ve işlem kaydı exchange_verified=False
        ile işaretlenir."""
        if self.exec_ is None or not self._mirror_live:
            return None
        try:
            res = self.exec_.place_market_order(symbol, side, qty,
                                                reduce_only=reduce_only)
            self.exec_stats["orders_sent"] += 1
            self.exec_stats["last_ok_utc"] = datetime.now(timezone.utc).isoformat()
            return res
        except Exception as exc:  # noqa: BLE001 — fail-open katman sınırı
            self.exec_stats["orders_failed"] += 1
            msg = str(exc)[:200]
            self.exec_stats["last_error"] = msg
            print(f"[kaos-exec] mirror FAILED: {msg}", flush=True)
            return {"ok": False, "order_id": None, "status": "failed",
                    "avg_price": None, "error": msg}

    def _paper_open_symbols(self) -> set[str]:
        """Şu an AÇIK kağıt pozisyonlarının sembolleri (restore dâhil;
        open_fills restore'da temizlendiği için gerçek kaynak portfolio)."""
        try:
            pos = self.runner.portfolio.positions
            if isinstance(pos, dict):
                return {str(s).upper() for s in pos.keys()}
            if isinstance(pos, (list, tuple)):
                out = set()
                for p in pos:
                    sym = getattr(p, "symbol", None)
                    out.add(str(sym if sym is not None else p).upper())
                return out
        except Exception:  # noqa: BLE001 — fail-open, aynalamayı etkilemesin
            return set()
        return set()

    def _flatten_orphans(self, tag: str) -> None:
        """Testnet'te açık ama kağıt tarafında karşılığı OLMAYAN pozisyonları
        kapat (reduce-only). Kağıda eşlik eden pozisyona DOKUNMAZ — aynalama
        sapması yalnız yetimlerde düzeltilir. Fail-open: hata loglanır,
        runner'a sıçramaz."""
        if self.exec_ is None or not self._mirror_live:
            return
        paper = self._paper_open_symbols()
        try:
            positions = self.exec_.get_open_positions()
        except Exception as exc:  # noqa: BLE001
            print(f"[kaos-exec] {tag} position check FAILED: {exc}", flush=True)
            return
        for pos in positions:
            sym = str(pos.get("symbol") or "").upper()
            if sym in paper:
                continue  # canlı kağıt pozisyonunun aynası — bot yönetiyor
            side = "SELL" if pos.get("side") == "long" else "BUY"
            try:
                self.exec_.place_market_order(sym, side,
                                              pos.get("contracts") or 0.0,
                                              reduce_only=True)
                print(f"[kaos-exec] {tag} orphan flatten: {sym} "
                      f"{pos.get('contracts')} kapatıldı (paper karşılığı yok)",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[kaos-exec] {tag} orphan flatten FAILED {sym}: {exc}",
                      flush=True)

    def _startup_flatten(self) -> None:
        """Aynalama açılırken testnet'te paper karşılığı olmayan KALINTI
        pozisyonlar sıfırlanır (paper restart'ta restore edilen açık
        pozisyonlarının aynalarına dokunulmaz)."""
        self._flatten_orphans("startup")

    def _reconcile_orphans(self) -> None:
        """Periyodik yetim temizliği (RECONCILE_EVERY_S): mirror kapanışı
        başarısız olan testnet pozisyonları bu düzeltmeyle kapanır."""
        now = time.monotonic()
        if now - self._last_reconcile < self.RECONCILE_EVERY_S:
            return
        self._last_reconcile = now
        self._flatten_orphans("reconcile")

    # ---- closed-trade pairing (harness math; s20 => no stop ratchet) -------
    def collect_closed(self) -> None:
        fills = self.ledger.fills
        while self.cursor < len(fills):
            i = self.cursor
            fill, intent = fills[i]
            self.cursor += 1
            if getattr(intent, "value", intent) == "open":
                # canlı giriş dolgusunu testnet'e aynala (yalnız canlı döngüde)
                m_entry = self._mirror(
                    fill.symbol,
                    "BUY" if fill.side.value == "buy" else "SELL", fill.qty)
                if m_entry is not None:
                    self.open_mirror[fill.symbol] = m_entry
                self.open_fills[fill.symbol] = (i, fill)
                continue
            got = self.open_fills.pop(fill.symbol, None)
            if got is None:
                continue
            oi, entry = got
            exit_px = fill.price
            close_fee = fill.fee
            levels = self.ledger.open_levels[oi] if oi < len(self.ledger.open_levels) else None
            if intent.value in ("stop", "take_profit") and levels is not None:
                stop_px, tp_px = levels
                level = stop_px if intent.value == "stop" else tp_px
                fee_bps, slip_bps = self._costs.get(fill.symbol, (self.cfg.fee_bps, self.cfg.slippage_bps))
                if fill.side.value == "sell":    # closing a long: worse = lower
                    exit_px = min(level * (1.0 - slip_bps / 10_000.0), fill.price)
                else:                            # closing a short: worse = higher
                    exit_px = max(level * (1.0 + slip_bps / 10_000.0), fill.price)
                close_fee = abs(exit_px * fill.qty) * (fee_bps / 10_000.0)
            qty = fill.qty
            is_long = entry.side.value == "buy"
            if is_long:
                pnl = (exit_px - entry.price) * qty - entry.fee - close_fee
            else:
                pnl = (entry.price - exit_px) * qty - entry.fee - close_fee
            rec = {
                "symbol": fill.symbol.split(":", 1)[-1],
                "side": "LONG" if is_long else "SHORT",
                "qty": _f(qty),
                "notional_usd": _f(qty * entry.price),
                "fees_usd": _f(entry.fee + close_fee),
                "entry_price": _f(entry.price),
                "exit_price": _f(exit_px),
                "pnl_usd": _f(pnl),
                "exit_reason": intent.value,
                "exit_utc": datetime.fromtimestamp(fill.ts_ns / _NS, tz=timezone.utc).isoformat(),
            }
            key = _closed_key(rec)
            if key in self._closed_keys:
                continue   # regenerated by a restart replay — already persisted
            # restart sonrası ilk 300 bar REPLAY'tir (warmup+geçmiş); bu
            # barlardan doğan işlemler canlı sinyal değildir — sayma.
            if getattr(self, "_replay_guard_until_ms", 0) and                     fill.ts_ns // 10**6 <= self._replay_guard_until_ms:
                self._closed_keys.add(key)
                continue
            self._closed_keys.add(key)
            self.closed_all.append(rec)
            # canlı kapanışı testnet'e aynala + borsa kanıtını kayda işle
            # (yalnız aynalama açıksa alanlar eklenir; kapalıyken rec değişmez)
            if self.exec_ is not None:
                m_entry = self.open_mirror.pop(fill.symbol, None)
                # giriş testnet'te min-notional için BÜYÜTÜLDÜYSE çıkış aynı
                # boyutta olmalı (artık pozisyon kalmasın)
                exit_qty = qty
                if isinstance(m_entry, dict) and m_entry.get("bumped"):
                    exit_qty = float(m_entry.get("qty_used") or qty)
                m_exit = self._mirror(fill.symbol,
                                      "SELL" if is_long else "BUY", exit_qty,
                                      reduce_only=True)
                rec["origin"] = "live"
                rec["exchange_entry_order_id"] = (m_entry or {}).get("order_id")
                rec["exchange_order_id"] = (m_exit or {}).get("order_id")
                rec["exchange_verified"] = bool(
                    (m_exit or {}).get("ok")) and bool(
                    (m_entry or {}).get("ok"))
                notes = []
                if isinstance(m_entry, dict) and m_entry.get("bumped"):
                    notes.append("testnet emri min notional için "
                                 "%s→%s büyütüldü" % (_f(qty), _f(exit_qty)))
                if not rec["exchange_verified"]:
                    notes.append((m_entry or {}).get("error")
                                 or (m_exit or {}).get("error")
                                 or "mirror failed")
                if notes:
                    rec["exchange_note"] = "; ".join(notes)

    # ---- startup: 300 bars (100 warmup + 200 evaluated) --------------------
    def start(self) -> dict[str, list[list[Any]]]:
        now_ms = int(time.time() * 1000)
        total = WARMUP_BARS + EVAL_BARS
        per: dict[str, list[list[Any]]] = {}
        for raw in self.order:
            ks = self._fetch_with_backoff(total, now_ms, raw)
            per[raw] = ks
            print(f"[live-paper] {raw}: {len(ks)} closed bars "
                  f"({datetime.fromtimestamp(ks[0][0] / 1000, tz=timezone.utc):%Y-%m-%d %H:%M} -> "
                  f"{datetime.fromtimestamp(ks[-1][6] / 1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC)",
                  flush=True)
        n = min(len(v) for v in per.values())
        if any(len(v) < total for v in per.values()):
            print(f"[live-paper] WARNING: trimming all symbols to common length {n}", flush=True)
            per = {r: v[-n:] for r, v in per.items()}

        warm = {r: v[:WARMUP_BARS] for r, v in per.items()}
        ev = {r: v[WARMUP_BARS:] for r, v in per.items()}
        warm_bars = min(len(v) for v in warm.values())

        print(f"[live-paper] warmup: feeding {warm_bars} bars x {len(self.order)} symbols ...", flush=True)
        warm_by_open = {r: self._bars_by_open(v[:warm_bars]) for r, v in warm.items()}
        for open_ms in sorted(set(k for m in warm_by_open.values() for k in m)):
            self._feed_bar(warm_by_open, open_ms)
        if self.runner.portfolio.positions:
            first_ts = min(int(v[0][0]) for v in ev.values()) * 1_000_000
            ha._liquidate_open_positions(self.runner, self.ledger, first_ts,
                                         notify_reason="warmup_liquidation")
            print("[live-paper] warmup-boundary positions liquidated", flush=True)
        # restart replays: bars fed during startup (warmup+history) mark trades
        # that must never count as live signals
        self._replay_guard_until_ms = int(time.time() * 1000)
        # NOT: restore edilen orijinal egri noktalari KORUNUR; yalnizca
        # replay feed'inin YENI noktalari _append_equity'de bastirilir.
        # evaluated window only from here: cursor skips warmup + boundary fills
        self.cursor = len(self.ledger.fills)
        self.open_fills.clear()
        # Aynalama yalnız BURADAN sonra (canlı poll döngüsü) açılır: startup
        # replay dolguları geçmiş sinyallerdir, şimdi emre çevrilemez. Açılır
        # açılmaz testnet kalıntısı pozisyonlar da sıfırlanır (paper flat başlar).
        self._mirror_live = True
        if self.exec_ is not None:
            self._startup_flatten()

        print(f"[live-paper] evaluated: feeding {len(ev[self.order[0]])} bars ...", flush=True)
        ev_by_open = {r: self._bars_by_open(v) for r, v in ev.items()}
        for open_ms in sorted(set(k for m in ev_by_open.values() for k in m)):
            self._feed_bar(ev_by_open, open_ms)
            self.collect_closed()
            eq = self.runner.portfolio.snapshot((open_ms + BAR_MS - 1) * 1_000_000).equity
            self._append_equity(open_ms + BAR_MS, eq)
        self.last_fed_open_ms = max(int(v[-1][0]) for v in ev.values())
        return ev

    # ---- live: feed every newly closed bar (catch-up gaps in order) --------
    def poll_bars(self, now_ms: int) -> bool:
        target_open = ((now_ms - POST_CLOSE_DELAY_MS) // BAR_MS) * BAR_MS
        need = (target_open - self.last_fed_open_ms) // BAR_MS
        if need <= 0:
            return False
        n = int(min(need, MAX_CATCHUP_BARS))
        if need > MAX_CATCHUP_BARS:
            print(f"[live-paper] WARNING: backlog {need} bars > cap, replaying last {n}", flush=True)
        by_open: dict[str, dict[int, list[Any]]] = {}
        for raw in self.order:
            ks = self._fetch_with_backoff(n, now_ms, raw)
            by_open[raw] = {int(k[0]): k for k in ks
                            if self.last_fed_open_ms < int(k[0]) <= target_open}
        fed = 0
        fed_opens: list[int] = []
        for open_ms in sorted(set(k for m in by_open.values() for k in m)):
            missing = [r for r in self.order if open_ms not in by_open[r]]
            if missing:
                print(f"[live-paper] WARNING: bar {open_ms} missing for "
                      f"{','.join(missing)} — feeding without them", flush=True)
            self._feed_bar(by_open, open_ms)
            self.collect_closed()
            eq = self.runner.portfolio.snapshot((open_ms + BAR_MS - 1) * 1_000_000).equity
            self._append_equity(open_ms + BAR_MS, eq)
            fed += 1
            fed_opens.append(open_ms)
        # Advance ONLY to what was actually fed: a bar that raced its own
        # close (target computed, kline not published yet) must be retried on
        # the next poll, never skipped.
        if fed_opens:
            self.last_fed_open_ms = max(fed_opens)
        if fed:
            print(f"[live-paper] fed {fed} bar(s) up to open {target_open} "
                  f"({datetime.fromtimestamp(target_open / 1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC)",
                  flush=True)
        return fed > 0

    # ---- restart persistence -----------------------------------------------
    def restore_from(self, st: dict[str, Any]) -> None:
        """Restore the monitor-facing series from the previous state.json:
        equity_history (last 500), closed_trades (last 40) and the trades_today
        counter. The equity base itself is applied by main() as the starting
        cash. Open positions are NOT restorable (mark/pnl is instantaneous
        state): the paper account restarts FLAT and continues from the last
        known equity_usd — logged honestly below."""
        hist = st.get("equity_history")
        if isinstance(hist, list):
            # Denetim kurali (2026-09-06): egri YALNIZ canli donemi kapsar.
            # Canli donem baslangici = onceki state'teki live_base_equity
            # zamanina esit veya sonrasi (ilk nokta canli baslangic equity'si).
            cut_ms = None
            for p in hist:
                try:
                    if float(p[1]) == float(st.get("live_base_equity") or -1):
                        cut_ms = float(p[0]); break
                except (TypeError, ValueError):
                    continue
            for p in hist[-EQUITY_HISTORY_MAX:]:
                try:
                    ts, eq = float(p[0]), float(p[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if math.isfinite(ts) and math.isfinite(eq):
                    if cut_ms is None or ts >= cut_ms:
                        self.equity_history.append([ts, eq])
        # Canli-donem muhasebesi (denetim oncesi replay donemi egriden ayrilir):
        base = st.get("live_base_equity")
        if base:
            self.live_base_equity = float(base)
        self.equity_note = st.get("equity_history_note")
        trades = st.get("closed_trades")
        if isinstance(trades, list):
            for t in trades[-CLOSED_TRADES_MAX:]:
                if not isinstance(t, dict):
                    continue
                try:
                    rec = {
                        "symbol": str(t["symbol"]),
                        "side": str(t["side"]),
                        "qty": _f(t.get("qty", 0.0)),
                        "notional_usd": _f(t.get("notional_usd", 0.0)),
                        "fees_usd": _f(t.get("fees_usd", 0.0)),
                        "entry_price": _f(t["entry_price"]),
                        "exit_price": _f(t["exit_price"]),
                        "pnl_usd": _f(t["pnl_usd"]),
                        "exit_reason": str(t["exit_reason"]),
                        "exit_utc": str(t["exit_utc"]),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                rec["origin"] = "restored"   # canlı sinyal DEĞİL (restart kalıntısı)
                key = _closed_key(rec)
                if key not in self._closed_keys:
                    self._closed_keys.add(key)
                    self.closed_all.append(rec)
        # trades_today: the persisted counter already includes today's trades
        # that aged out of the 40-trade window — keep that excess as the base
        # so the counter stays exact after the restart.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        st_day = str(st.get("ts_utc", ""))[:10]
        try:
            counter = int(st.get("trades_today", 0))
        except (TypeError, ValueError):
            counter = 0
        if st_day == today and counter > 0:
            in_list = sum(1 for t in self.closed_all
                          if t["exit_utc"][:10] == st_day)
            self._trades_today_base = max(counter - in_list, 0)
            self._trades_today_day = today
        try:
            eq0 = _f(st.get("equity_usd"))
        except (TypeError, ValueError):
            eq0 = 0.0
        n_open = len(st.get("positions") or [])
        print(f"[live-paper] state restored: equity base ${eq0:.2f}, "
              f"{len(self.closed_all)} closed trades, "
              f"{len(self.equity_history)} equity points, "
              f"trades_today base {self._trades_today_base}", flush=True)
        print(f"[live-paper] AÇIK POZİSYONLAR restart'ta sıfırlanır — paper "
              f"({n_open} pozisyon devralınmadı)", flush=True)

    def _append_equity(self, ts_ms: float, equity: float) -> None:
        """Append one equity point, replacing any earlier point with the same
        timestamp (a restart re-feeds recent bars: the re-evaluated point
        wins and the series never grows duplicate timestamps).
        DENETIM KURALI (2026-09-06): startup replay (guard suresi icindeki
        barlar) eegriye YAZILMAZ — replay kazancari canli donemle
        karistirmaz; guard bitince normal ekleme surer."""
        ts_ms = float(ts_ms)
        guard = getattr(self, "_replay_guard_until_ms", 0)
        if guard and ts_ms <= guard:
            return
        self.equity_history = [p for p in self.equity_history if p[0] != ts_ms]
        self.equity_history.append([ts_ms, _f(equity)])
        excess = len(self.equity_history) - EQUITY_HISTORY_MAX
        if excess > 0:
            del self.equity_history[:excess]  # state reports last 500 only

    # ---- state -------------------------------------------------------------
    def _cap_closed(self) -> None:
        """Bound the in-memory trade lists (long-run memory safety): closed_all
        is trimmed to CLOSED_TRADES_KEEP and _closed_keys is rebuilt from the
        kept records. trades_today stays EXACT: same-day trades trimmed out of
        the list are folded into _trades_today_base (called right after the
        day-rollover reset in build_state, so the base day is current here).
        In-process bars are never re-fed (double-feed guard), so the dropped
        keys can never regenerate a duplicate."""
        excess = len(self.closed_all) - CLOSED_TRADES_KEEP
        if excess > 0:
            dropped = self.closed_all[:excess]
            del self.closed_all[:excess]
            day = self._trades_today_day
            if day is not None:
                self._trades_today_base += sum(
                    1 for t in dropped if t["exit_utc"][:10] == day)
            self._closed_keys = {_closed_key(t) for t in self.closed_all}

    def _exchange_section(self) -> dict | None:
        """state.json 'exchange' bölümü: aynalama durumu + 60 sn'de bir taze
        bakiye. Fail-open: bakiye hatası bölümü bozmaz (None alanlar)."""
        if self.exec_ is None:
            return None
        now = time.time()
        if (self._last_balance_refresh is None
                or now - self._last_balance_refresh >= 60.0):
            self._last_balance_refresh = now
            try:
                self._exec_balance = self.exec_.get_balance()
            except Exception as exc:  # noqa: BLE001
                self._exec_balance = None
                self.exec_stats["last_error"] = str(exc)[:200]
        bal = self._exec_balance or {}
        return {
            "enabled": True,
            "network": "futures_testnet",
            "wallet_usdt": _f(bal.get("wallet")) if bal else None,
            "available_usdt": _f(bal.get("available")) if bal else None,
            "orders_sent": int(self.exec_stats["orders_sent"]),
            "orders_failed": int(self.exec_stats["orders_failed"]),
            "last_ok_utc": self.exec_stats["last_ok_utc"],
            "last_error": self.exec_stats["last_error"],
        }

    def build_state(self) -> dict[str, Any]:
        self.collect_closed()
        now_ms = int(time.time() * 1000)
        snap = self.runner.portfolio.snapshot(now_ms * 1_000_000)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._trades_today_day != today:
            self._trades_today_base = 0
            self._trades_today_day = today
        self._cap_closed()
        positions = []
        for v in snap.positions:
            raw = v.symbol.split(":", 1)[-1]
            mark = self.last_close.get(raw, v.mark_px)
            if v.side is PositionSide.LONG:
                upnl = (mark - v.entry_px) * v.qty
            else:
                upnl = (v.entry_px - mark) * v.qty
            notional = v.qty * v.entry_px
            roe = (upnl / notional * 100.0) if notional else None
            stop_px = float(getattr(v, "stop_px", 0) or 0)
            tp_px = float(getattr(v, "tp_px", 0) or 0)
            entry_ts = getattr(v, "entry_ts_ns", None)
            bars = int(max(0, (now_ms * 10**6 - entry_ts) // (900 * 10**9))) if entry_ts else 0
            positions.append({
                "symbol": raw,
                "side": "LONG" if v.side is PositionSide.LONG else "SHORT",
                "qty": _f(v.qty),
                "entry_price": _f(v.entry_px),
                "mark_price": _f(mark),
                "unrealized_pnl_usd": _f(upnl),
                # enrichment for futures cards (notional/roe/sl/tp/bars)
                "notional_usd": _f(notional),
                "roe_pct": _f(roe) if roe is not None else None,
                "sl_price": _f(stop_px),
                "tp_price": _f(tp_px),
                "bars_held": bars,
            })
        return {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "PAPER_LIVE",
            "run_control": self._control_snapshot(),
            "equity_usd": _f(snap.equity),
            "day_pnl_usd": _f(snap.daily_pnl),
            "trades_today": self._trades_today_base + sum(
                1 for t in self.closed_all if t["exit_utc"][:10] == today),
            "positions": positions,
            "equity_history": self.equity_history[-EQUITY_HISTORY_MAX:],
            "live_base_equity": (
                _f(self.live_base_equity) if self.live_base_equity is not None
                else _f(min((e for _, e in self.equity_history), default=100.0))),
            "equity_history_note": self.equity_note,
            "closed_trades": self.closed_all[-CLOSED_TRADES_MAX:],
            "exchange": self._exchange_section(),
        }

    # ---- run control (AYARLAR ile uzaktan yonetilen mod/zamanlama) --------
    def _reload_schedule(self) -> None:
        try:
            m = self.schedule_path.stat().st_mtime
        except OSError:
            return
        if m == self._schedule_mtime:
            return
        self._schedule_mtime = m
        try:
            d = json.loads(self.schedule_path.read_text(encoding="utf-8"))
            mode = str(d.get("run_mode", "active")).lower()
            if mode not in ("active", "paused", "scheduled"):
                mode = "active"
            self._schedule = {
                "run_mode": mode,
                "utc_start": d.get("utc_start"),
                "utc_end": d.get("utc_end"),
                "updated_utc": d.get("updated_utc"),
            }
            print(f"[live-paper] run control: {self._schedule}", flush=True)
        except Exception as exc:
            print(f"[live-paper] schedule.json okunamadi (onceki ayar surer): {exc!r}",
                  flush=True)

    def _effective_run_mode(self) -> str:
        """'paper' = girişler açık; aksi halde girişlerin kapalı olduğu mod
        adı ('paused' / 'outside_window'). Exits her zaman serbest."""
        self._reload_schedule()
        mode = self._schedule.get("run_mode", "active")
        if mode == "paused":
            return "paused"
        if mode == "scheduled":
            h = datetime.now(timezone.utc).hour
            a, b = self._schedule.get("utc_start"), self._schedule.get("utc_end")
            try:
                a_i, b_i = int(a), int(b)
            except (TypeError, ValueError):
                return "paper"  # bozuk pencere -> güvenli tarafta açık
            if a_i == b_i:
                return "paper"   # 24h açık
            in_win = (a_i <= h < b_i) if a_i < b_i else (h >= a_i or h < b_i)
            return "paper" if in_win else "outside_window"
        return "paper"

    def _control_snapshot(self) -> dict:
        self._reload_schedule()
        eff = self._effective_run_mode()
        return {
            "run_mode": self._schedule.get("run_mode", "active"),
            "effective": ("trading" if eff == "paper" else eff),
            "utc_start": self._schedule.get("utc_start"),
            "utc_end": self._schedule.get("utc_end"),
        }

    def write_state(self) -> None:
        """Atomic state write, resilient to disk-full/OSError: any failure is
        logged and swallowed (previous state.json stays intact, the tmp file
        is cleaned up) — a failed write must never crash the bot."""
        tmp: Path | None = None
        try:
            state = self.build_state()
            path = self.state_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            data = json.dumps(state, allow_nan=False)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception as e:
            print(f"[live-paper] WARNING: state write failed "
                  f"({type(e).__name__}: {e}) — keeping previous state.json",
                  flush=True)
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Continuous live-paper s20 runner "
                                             "(state: reports/live-paper/state.json)")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS,
                    help="comma-separated Binance spot symbols (default: "
                         f"{DEFAULT_SYMBOLS})")
    ap.add_argument("--cash", type=float, default=100.0,
                    help="paper starting cash USD (default 100)")
    ap.add_argument("--once", action="store_true",
                    help="run startup (300-bar warmup+eval feed), write state "
                         "once and exit (no loop; testing)")
    args = ap.parse_args()

    raws = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not raws:
        ap.error("--symbols must name at least one symbol")
    out_dir = REPO / "reports" / "live-paper"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Restart persistence: read the previous state.json (corrupt → .broken
    # backup + fresh start) and continue equity from the last known equity_usd.
    prev = load_previous_state(out_dir / "state.json")
    base_cash = args.cash
    if prev is not None:
        try:
            eq0 = _f(prev.get("equity_usd"))
        except (TypeError, ValueError):
            eq0 = 0.0
        if math.isfinite(eq0) and eq0 > 0.0:
            base_cash = eq0
        else:
            print(f"[live-paper] WARNING: previous equity_usd unusable "
                  f"({prev.get('equity_usd')!r}) — starting from --cash "
                  f"${args.cash:.2f}", flush=True)

    lp = LivePaper(raws, base_cash, out_dir)
    print(f"[live-paper] s20 live paper: symbols={','.join(raws)} cash=${base_cash:.2f} "
          f"state={lp.state_path}", flush=True)
    if prev is not None:
        lp.restore_from(prev)
    try:
        lp.start()
        lp.write_state()
        snap = lp.runner.portfolio.snapshot(int(time.time() * 1000) * 1_000_000)
        print(f"[live-paper] startup done: equity ${snap.equity:.2f}, "
              f"{len(snap.positions)} open, {len(lp.closed_all)} closed trades "
              f"({len(lp.equity_history)} equity points)", flush=True)
        if args.once:
            print(f"[live-paper] --once: state written to {lp.state_path}", flush=True)
            return

        last_write = 0.0
        t0 = time.time()
        last_hb = t0
        while True:
            now_ms = int(time.time() * 1000)
            try:
                lp._reconcile_orphans()
                if lp.poll_bars(now_ms):
                    lp.write_state()
                    last_write = time.time()
                    snap = lp.runner.portfolio.snapshot(int(time.time() * 1000) * 1_000_000)
                    print(f"[live-paper] equity ${snap.equity:.2f} "
                          f"(day {snap.daily_pnl:+.2f}), {len(snap.positions)} open, "
                          f"{len(lp.closed_all)} closed total", flush=True)
            except Exception:
                print("[live-paper] ERROR in bar poll (will retry):", flush=True)
                traceback.print_exc()
                sys.stdout.flush()
            if time.time() - last_write >= STATE_HEARTBEAT_S:
                try:
                    lp.write_state()
                    last_write = time.time()
                except Exception:
                    print("[live-paper] ERROR writing state (will retry):", flush=True)
                    traceback.print_exc()
                    sys.stdout.flush()
            if time.time() - last_hb >= HEARTBEAT_S:
                try:
                    up_s = time.time() - t0
                    hsnap = lp.runner.portfolio.snapshot(
                        int(time.time() * 1000) * 1_000_000)
                    print(f"[heartbeat] up {int(up_s // 3600)}h "
                          f"{int((up_s % 3600) // 60)}m, "
                          f"equity ${hsnap.equity:.2f}, "
                          f"trades={len(lp.closed_all)}", flush=True)
                except Exception:
                    print("[live-paper] ERROR building heartbeat (continuing):",
                          flush=True)
                    traceback.print_exc()
                    sys.stdout.flush()
                last_hb = time.time()
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        # Graceful stop anywhere (startup feed, sleep, poll): one final atomic
        # state write so the monitor file always reflects the last known state.
        try:
            lp.write_state()
        except Exception:
            pass
        print("[live-paper] stopped (Ctrl+C) — final state written", flush=True)


if __name__ == "__main__":
    main()
