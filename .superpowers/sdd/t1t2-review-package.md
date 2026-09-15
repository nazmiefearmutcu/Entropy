# commits
877d88c fix(scripts): pessimistic intrabar barrier resolution + level fills in accuracy harness
9aee08e fix(bot): reset consensus trail peak on entry and close, track it through min_hold

# stat
 scripts/entropy_accuracy_btc15m.py      | 287 ++++++++++++++++++++++++-------
 src/entropy/bot/strategies/consensus.py |  24 ++-
 tests/bot/test_accuracy_harness.py      | 289 ++++++++++++++++++++++++++++++++
 tests/bot/test_consensus.py             | 115 +++++++++++++
 4 files changed, 647 insertions(+), 68 deletions(-)

# full diff
diff --git a/scripts/entropy_accuracy_btc15m.py b/scripts/entropy_accuracy_btc15m.py
index d50042f..d932e4e 100644
--- a/scripts/entropy_accuracy_btc15m.py
+++ b/scripts/entropy_accuracy_btc15m.py
@@ -14,6 +14,29 @@ cost-aware risk layer) with real Binance 15m klines:
 Usage:
   .venv/bin/python scripts/entropy_accuracy_btc15m.py --bars 2880 --out /tmp/entropy_accuracy/30d
   .venv/bin/python scripts/entropy_accuracy_btc15m.py --bars 96 --end-date 2026-08-02T00:00:00Z --out /tmp/entropy_accuracy/1d
+
+Honest-measurement conventions (these intentionally lower the measured win
+rate versus the original harness — that is the point):
+
+  * Pessimistic intrabar barrier resolution. Each 15m bar is fed as 4 ticks
+    O, L, H, C — the stop-tripping tick BEFORE the take-profit-tripping tick.
+    When a bar's range contains both an open position's stop and its
+    take-profit, the STOP resolves first (a same-bar entry whose first bar
+    spans both barriers is covered too, which a per-bar pre-scan of
+    already-open positions would miss).
+  * Level fills, not bar extremes. Exit fills from mechanical stop/TP intents
+    are clamped, when pairing, to the barrier level the risk layer anchored at
+    entry (worse of level and actual fill, both with the close-side adverse
+    slippage the executor charges). Strategy exits (intent "close") and the
+    end-of-test liquidation are untouched.
+  * Zero PnL is a loss: only ``pnl > 0`` counts as a win (reported as
+    ``win_definition``).
+  * ``--warmup-bars N`` (default 100) seeds strategy/risk state with N extra
+    bars before the evaluated window; warmup trades are not counted and a
+    position still open at the window start is liquidated at the first
+    evaluated tick. ``--warmup-bars 0`` restores the old cold-start behavior.
+  * Long-only metrics (``win_rate_long_only``) report the spot-deployable
+    subset alongside the total.
 """
 
 from __future__ import annotations
@@ -34,7 +57,7 @@ sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
 
 from entropy.bot.calibration import DummyLedger
 from entropy.bot.config import BotConfig, ConsensusConfig, MarketCostConfig, RiskOverrides
-from entropy.bot.orders import OrderSide
+from entropy.bot.orders import Fill, OrderIntent, OrderSide
 from entropy.bot.portfolio import PositionSide
 from entropy.bot.runner import BotRunner
 
@@ -105,13 +128,25 @@ def fetch_klines(bars: int, end_ms: int, cache: Path | None = None,
 
 
 def build_ticks(klines: list[list[Any]], symbol: str = SYMBOL) -> list[dict[str, Any]]:
+    """One 15m kline -> 4 ticks: O, L, H, C.
+
+    The LOW tick is fed BEFORE the HIGH tick on purpose (pessimistic intrabar
+    convention): the risk layer compares the running mark against an open
+    position's stop and take-profit on every tick, so with the historical
+    O,H,L,C order a take-profit inside the bar always resolved BEFORE a stop
+    also inside the bar, at the bar's extreme price. Feeding L first makes the
+    STOP resolve first whenever both barriers sit inside one bar's [low, high]
+    — including bars where the position was opened at the open tick — and the
+    remaining level-fill clamp in simulate() keeps mechanical fills honest.
+    Strategy bars bucket on ts//900s so all four ticks land in the same 15m
+    bar regardless of order.
+    """
     ticks: list[dict[str, Any]] = []
     for k in klines:
         open_ms = int(k[0])
         open_px, high, low, close = float(k[1]), float(k[2]), float(k[3]), float(k[4])
-        # open, high, low, close as 4 ticks inside the bar; strategy bars bucket
-        # on ts//900s so all four land in the same 15m bar.
-        for off_s, px in ((0, open_px), (1, high), (2, low), (3, close)):
+        # open, low, high, close as 4 ticks inside the bar (stop before TP)
+        for off_s, px in ((0, open_px), (1, low), (2, high), (3, close)):
             ticks.append({
                 "symbol": symbol, "price": px, "amount": 1.0, "side": "buy",
                 "ts_ns": (open_ms + off_s * 1000) * 1_000_000,
@@ -123,47 +158,44 @@ def utc_day(ts_ns: int) -> str:
     return datetime.fromtimestamp(ts_ns / _NS, tz=timezone.utc).strftime("%Y-%m-%d")
 
 
-def simulate(klines: list[list[Any]], cfg: BotConfig,
-             run_dir: str = "/tmp/entropy_accuracy/_sim/ledger",
-             trade_csv: str = "/tmp/entropy_accuracy/_sim/trades.csv",
-             symbol: str = SYMBOL) -> dict[str, Any]:
-    """Feed real 15m klines through the final-state BotRunner and compute the
-    full accuracy report. Mirrors the metric math of calibration.run_backtest
-    (end-of-test liquidation, paired fills, costs)."""
-    out_dir = Path(run_dir).parent
-    out_dir.mkdir(parents=True, exist_ok=True)
-    runner = BotRunner(cfg, run_dir=str(out_dir / "ledger"))
-    dummy = DummyLedger()
-    runner.ledger = dummy  # type: ignore[assignment]
-
-    ticks = build_ticks(klines, symbol=symbol)
-    equity_curve: list[tuple[int, float, float]] = []  # (ts_ns, equity, exposure)
-    max_exposure_pct = 0.0
-    max_open = 0
-    prev_day = None
-    print(f"[entropy-accuracy] feeding {len(ticks)} ticks ...")
-    for t in ticks:
-        day = utc_day(t["ts_ns"])
-        if day != prev_day:
-            # Backtest is fast-forwarded, so the runner's wall-clock day
-            # rollover never fires; reset per tick-day to keep the daily-loss
-            # kill-switch genuinely daily (same semantics as live).
-            if prev_day is not None:
-                runner.portfolio.reset_day()
-                runner.risk.reset_day()
-            runner._utc_day = day
-            prev_day = day
-        runner.on_trade(t["symbol"], t["price"], t["amount"], t["side"], t["ts_ns"])
-        snap = runner.portfolio.snapshot(t["ts_ns"])
-        equity_curve.append((t["ts_ns"], snap.equity, snap.open_count))
-        if snap.open_count > max_open:
-            max_open = snap.open_count
-        exp = runner.portfolio.exposure()
-        if snap.equity > 0 and exp / snap.equity > max_exposure_pct:
-            max_exposure_pct = exp / snap.equity
-
-    # ---- end-of-test liquidation (mirrors calibration.run_backtest) ----
-    final_ts = ticks[-1]["ts_ns"]
+class _BarrierLedger(DummyLedger):
+    """DummyLedger that remembers the stop/take-profit levels the risk layer
+    anchored at each open fill.
+
+    The runner prices mechanical exits at the mark that tripped them, so a
+    stop/TP fill recorded inside a wide bar carries the bar's extreme, not the
+    barrier. The levels (``RiskManager.stop_tp_prices`` on the
+    slippage-adjusted entry fill, volatility scaling included) are captured
+    from the live position at open-fill time — after ``portfolio.open`` — so
+    the anchoring math is mirrored exactly, and the pairing step in
+    :func:`simulate` can clamp mechanical exit fills back to the barrier.
+    """
+
+    def __init__(self, runner: BotRunner) -> None:
+        super().__init__()
+        self._runner = runner
+        # barrier levels captured at each OPEN fill, aligned 1:1 with
+        # self.fills (a symbol-keyed dict would be overwritten by later
+        # trades before the pairing walk ever reads it)
+        self.open_levels: list[tuple[float, float] | None] = []
+
+    def record_fill(self, fill: Any, intent: Any) -> None:
+        levels: tuple[float, float] | None = None
+        if getattr(intent, "value", intent) == "open":
+            pos = self._runner.portfolio.positions.get(fill.symbol)
+            if pos is not None:
+                levels = (pos.stop_px, pos.tp_px)
+        self.open_levels.append(levels)
+        super().record_fill(fill, intent)
+
+
+def _liquidate_open_positions(runner: BotRunner, ledger: DummyLedger, ts_ns: int,
+                              *, notify_reason: str | None = None) -> None:
+    """Flatten every open position at the mark, with close-side adverse
+    slippage and fees — the end-of-test liquidation math. Also used at the
+    warmup/window boundary so a position still open when the evaluated window
+    begins is liquidated at the window's first tick (its fill lands in the
+    warmup ledger and is not counted)."""
     executor = runner.executor
     for symbol in list(runner.portfolio.positions):
         pos = runner.portfolio.positions[symbol]
@@ -176,38 +208,139 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
         slip = mark_px * (slip_bps / 10_000.0)
         fill_px = mark_px - slip if close_side is OrderSide.SELL else mark_px + slip
         fee = abs(fill_px * pos.qty) * (fee_bps / 10_000.0)
-        from entropy.bot.orders import Fill, OrderIntent
-        dummy.record_fill(
+        ledger.record_fill(
             Fill(order_id=f"liq-{symbol}", symbol=symbol, side=close_side, qty=pos.qty,
-                 price=fill_px, fee=fee, slippage=slip, ts_ns=final_ts),
+                 price=fill_px, fee=fee, slippage=slip, ts_ns=ts_ns),
             OrderIntent.CLOSE,
         )
-        runner.portfolio.close(symbol, fill_px, final_ts, fee=fee)
+        runner.portfolio.close(symbol, fill_px, ts_ns, fee=fee)
+        if notify_reason is not None:
+            # the run continues after a warmup-boundary liquidation: re-arm the
+            # strategies exactly as the runner does for risk-originated closes
+            runner._notify_closed(symbol, notify_reason)
+
+
+def simulate(klines: list[list[Any]], cfg: BotConfig,
+             run_dir: str = "/tmp/entropy_accuracy/_sim/ledger",
+             trade_csv: str = "/tmp/entropy_accuracy/_sim/trades.csv",
+             symbol: str = SYMBOL, warmup_bars: int = 0) -> dict[str, Any]:
+    """Feed real 15m klines through the final-state BotRunner and compute the
+    full accuracy report. Mirrors the metric math of calibration.run_backtest
+    (end-of-test liquidation, paired fills, costs).
+
+    ``warmup_bars`` feeds the FIRST ``warmup_bars`` klines through the runner
+    without counting their trades (strategy/risk state carries into the
+    evaluated window; positions open at the window start are liquidated at the
+    window's first tick). ``warmup_bars=0`` is the legacy cold start.
+    """
+    out_dir = Path(run_dir).parent
+    out_dir.mkdir(parents=True, exist_ok=True)
+    runner = BotRunner(cfg, run_dir=str(out_dir / "ledger"))
+    warm_ledger = _BarrierLedger(runner)
+    dummy = _BarrierLedger(runner)
+    runner.ledger = warm_ledger  # type: ignore[assignment]
+
+    # Split into warmup and evaluated slices (keep at least one evaluated bar).
+    warmup_bars = max(0, min(warmup_bars, len(klines) - 1)) if klines else 0
+    warm_ticks = build_ticks(klines[:warmup_bars], symbol=symbol)
+    eval_ticks = build_ticks(klines[warmup_bars:], symbol=symbol)
+
+    def _feed(ticks: list[dict[str, Any]]):
+        curve: list[tuple[int, float, float]] = []
+        max_open = 0
+        max_exp = 0.0
+        prev_day = None
+        for t in ticks:
+            day = utc_day(t["ts_ns"])
+            if day != prev_day:
+                # Backtest is fast-forwarded, so the runner's wall-clock day
+                # rollover never fires; reset per tick-day to keep the daily-loss
+                # kill-switch genuinely daily (same semantics as live).
+                if prev_day is not None:
+                    runner.portfolio.reset_day()
+                    runner.risk.reset_day()
+                runner._utc_day = day
+                prev_day = day
+            runner.on_trade(t["symbol"], t["price"], t["amount"], t["side"], t["ts_ns"])
+            snap = runner.portfolio.snapshot(t["ts_ns"])
+            curve.append((t["ts_ns"], snap.equity, snap.open_count))
+            if snap.open_count > max_open:
+                max_open = snap.open_count
+            exp = runner.portfolio.exposure()
+            if snap.equity > 0 and exp / snap.equity > max_exp:
+                max_exp = exp / snap.equity
+        return curve, max_open, max_exp
+
+    warmup_trades = 0
+    if warmup_bars:
+        print(f"[entropy-accuracy] warmup: feeding {len(warm_ticks)} ticks "
+              f"({warmup_bars} bars) ...")
+        _feed(warm_ticks)
+        warmup_trades = len(warm_ledger.fills) // 2
+        if runner.portfolio.positions:
+            # flatten at the window's first tick, before the evaluated feed
+            _liquidate_open_positions(runner, warm_ledger, eval_ticks[0]["ts_ns"],
+                                      notify_reason="warmup_liquidation")
+    runner.ledger = dummy  # type: ignore[assignment]  # evaluated trades only
+
+    print(f"[entropy-accuracy] feeding {len(eval_ticks)} evaluated ticks ...")
+    equity_curve, max_open, max_exposure_pct = _feed(eval_ticks)
+
+    # ---- end-of-test liquidation (mirrors calibration.run_backtest) ----
+    final_ts = eval_ticks[-1]["ts_ns"]
+    _liquidate_open_positions(runner, dummy, final_ts)
 
     snap = runner.portfolio.snapshot(final_ts)
     total_trades = len(dummy.fills) // 2
     costs_paid = sum(f.fee + f.slippage * f.qty for f, _ in dummy.fills)
+    # Close-side adverse slippage the executor charges (PaperExecutor: slip =
+    # order price * slippage_bps/1e4 against the trade direction) — used to
+    # re-price mechanical stop/TP fills at their barrier level below.
+    resolved_costs = runner.executor.cost_model.for_symbol(symbol)
+    slip_bps = (
+        resolved_costs.slippage_bps if resolved_costs.slippage_bps is not None
+        else cfg.slippage_bps
+    )
     wins = losses = 0
+    long_trades = long_wins = 0
     total_profit = total_loss = 0.0
     closed_pnls: list[float] = []
     trades: list[dict[str, Any]] = []
     positions_history: dict[str, Any] = {}
+    # symbol -> levels captured (at record time) at that symbol's open fill
+    trade_levels: dict[str, tuple[float, float] | None] = {}
     exit_breakdown: Counter = Counter()
     hold_bars: list[float] = []
-    for fill, intent in dummy.fills:
+    for i, (fill, intent) in enumerate(dummy.fills):
         if intent.value == "open":
             positions_history[fill.symbol] = fill
+            trade_levels[fill.symbol] = dummy.open_levels[i]
         else:
             entry = positions_history.pop(fill.symbol)
+            exit_px = fill.price
+            levels = trade_levels.pop(fill.symbol, None)
+            if intent.value in ("stop", "take_profit") and levels is not None:
+                # Level fills, not bar extremes: clamp mechanical stop/TP exit
+                # fills to the barrier level the risk layer anchored at entry,
+                # taking the WORSE of the level fill and the actual fill (both
+                # carry the adverse close-side slippage). Strategy exits and
+                # the end-of-test liquidation (intent "close") are untouched.
+                stop_px, tp_px = levels
+                level = stop_px if intent.value == "stop" else tp_px
+                if fill.side.value == "sell":  # closing a long: worse = lower
+                    exit_px = min(level * (1.0 - slip_bps / 10_000.0), fill.price)
+                else:                          # closing a short: worse = higher
+                    exit_px = max(level * (1.0 + slip_bps / 10_000.0), fill.price)
             qty = fill.qty
-            if entry.side.value == "buy":
-                pnl = (fill.price - entry.price) * qty - entry.fee - fill.fee
+            side_str = "LONG" if entry.side.value == "buy" else "SHORT"
+            if side_str == "LONG":
+                pnl = (exit_px - entry.price) * qty - entry.fee - fill.fee
             else:
-                pnl = (entry.price - fill.price) * qty - entry.fee - fill.fee
+                pnl = (entry.price - exit_px) * qty - entry.fee - fill.fee
             closed_pnls.append(pnl)
             trades.append({
-                "side": "LONG" if entry.side.value == "buy" else "SHORT",
-                "entry_px": round(entry.price, 2), "exit_px": round(fill.price, 2),
+                "side": side_str,
+                "entry_px": round(entry.price, 2), "exit_px": round(exit_px, 2),
                 "qty": round(qty, 6),
                 "entry_ts": datetime.fromtimestamp(entry.ts_ns / _NS, tz=timezone.utc).isoformat(),
                 "exit_ts": datetime.fromtimestamp(fill.ts_ns / _NS, tz=timezone.utc).isoformat(),
@@ -216,6 +349,12 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
             })
             exit_breakdown[intent.value] += 1
             hold_bars.append((fill.ts_ns - entry.ts_ns) / (900 * _NS))
+            if side_str == "LONG":
+                long_trades += 1
+                if pnl > 0:
+                    long_wins += 1
+            # Zero PnL is a loss: only strictly positive pnl counts as a win
+            # (conservative — costs make exact-zero rare but possible).
             if pnl > 0:
                 wins += 1
                 total_profit += pnl
@@ -224,6 +363,7 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
                 total_loss += abs(pnl)
 
     win_rate = wins / total_trades if total_trades else 0.0
+    win_rate_long_only = long_wins / long_trades if long_trades else 0.0
     profit_factor = total_profit / total_loss if total_loss > 0 else (total_profit if total_profit > 0 else 1.0)
     if len(closed_pnls) > 1:
         mean_pnl = sum(closed_pnls) / len(closed_pnls)
@@ -260,6 +400,9 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
             "total_return_pct": round(total_return, 4),
             "total_trades": total_trades,
             "win_rate": round(win_rate, 4),
+            "win_definition": "pnl > 0",
+            "win_rate_long_only": round(win_rate_long_only, 4),
+            "total_trades_long_only": long_trades,
             "profit_factor": round(profit_factor, 4),
             "sharpe": round(sharpe, 4),
             "costs_paid": round(costs_paid, 4),
@@ -277,13 +420,20 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
         "exit_breakdown": dict(exit_breakdown),
         "avg_hold_bars": round(sum(hold_bars) / len(hold_bars), 2) if hold_bars else 0.0,
         "rejects": dict(Counter(r for _, r in dummy.rejects)),
+        "warmup": {"bars": warmup_bars, "trades": warmup_trades},
     }
     return report
 
 
 def main() -> None:
     ap = argparse.ArgumentParser()
-    ap.add_argument("--bars", type=int, default=2880, help="number of 15m bars")
+    ap.add_argument("--bars", type=int, default=2880,
+                    help="number of EVALUATED 15m bars (warmup bars are extra)")
+    ap.add_argument("--warmup-bars", type=int, default=100,
+                    help="extra bars fed before the evaluated window to seed "
+                         "strategy/risk state; their trades are NOT counted and "
+                         "a position open at the window start is liquidated at "
+                         "the first evaluated tick (0 = legacy cold start)")
     ap.add_argument("--symbol", default="BTCUSDT",
                     help="Binance spot symbol, e.g. BTCUSDT, ETHUSDT")
     ap.add_argument("--end-date", default="now")
@@ -323,10 +473,14 @@ def main() -> None:
 
     end_ms = parse_end(args.end_date)
     cache = None if args.skip_fetch else out_dir / "klines.json"
-    print(f"[entropy-accuracy] fetching {args.bars} x 15m {raw} bars ending {end_ms} ...")
-    klines = fetch_klines(args.bars, end_ms, cache, raw=raw)
-    print(f"[entropy-accuracy] got {len(klines)} closed bars "
-          f"({datetime.fromtimestamp(klines[0][0]/1000, tz=timezone.utc):%Y-%m-%d %H:%M} -> "
+    total_bars = args.bars + max(0, args.warmup_bars)
+    print(f"[entropy-accuracy] fetching {total_bars} x 15m {raw} bars ending {end_ms} ...")
+    klines = fetch_klines(total_bars, end_ms, cache, raw=raw)
+    warmup_bars = max(0, min(args.warmup_bars, len(klines) - 1)) if klines else 0
+    eval_klines = klines[warmup_bars:]
+    print(f"[entropy-accuracy] got {len(eval_klines)} evaluated closed bars "
+          f"(+{warmup_bars} warmup) "
+          f"({datetime.fromtimestamp(eval_klines[0][0]/1000, tz=timezone.utc):%Y-%m-%d %H:%M} -> "
           f"{datetime.fromtimestamp(klines[-1][6]/1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC)")
 
     strategies = tuple(args.strategy) if args.strategy else ("consensus",)
@@ -374,10 +528,12 @@ def main() -> None:
     )
 
     report = simulate(klines, cfg, run_dir=str(out_dir / "ledger"),
-                      trade_csv=str(out_dir / "trades.csv"), symbol=symbol)
+                      trade_csv=str(out_dir / "trades.csv"), symbol=symbol,
+                      warmup_bars=args.warmup_bars)
     report["config"] = {
-        "symbol": symbol, "interval": INTERVAL, "bars": len(klines),
-        "start_utc": datetime.fromtimestamp(klines[0][0] / 1000, tz=timezone.utc).isoformat(),
+        "symbol": symbol, "interval": INTERVAL, "bars": len(eval_klines),
+        "warmup_bars": warmup_bars,
+        "start_utc": datetime.fromtimestamp(eval_klines[0][0] / 1000, tz=timezone.utc).isoformat(),
         "end_utc": datetime.fromtimestamp(klines[-1][6] / 1000, tz=timezone.utc).isoformat(),
         "starting_cash": args.cash,
         "timeframe": "15m", "bar_s": 900.0,
@@ -403,7 +559,10 @@ def main() -> None:
     print(f"risk         : {args.per_trade_pct}%/trade, max {args.max_concurrent} open, "
           f"exposure <= {args.max_total_exposure_pct}%, daily loss <= {args.max_daily_loss_pct}%")
     print(f"final equity : ${m['final_equity']:.2f}  (return {m['total_return_pct']:+.2f}%)")
-    print(f"accuracy     : win rate {m['win_rate']*100:.1f}%  ({m['total_trades']} closed trades)")
+    print(f"accuracy     : win rate {m['win_rate']*100:.1f}%  ({m['total_trades']} closed trades, "
+          f"win = pnl > 0)")
+    print(f"long-only    : win rate {m['win_rate_long_only']*100:.1f}%  "
+          f"({m['total_trades_long_only']} closed long trades, spot-deployable)")
     print(f"quality      : profit factor {m['profit_factor']:.2f}, sharpe {m['sharpe']:.2f}")
     print(f"costs paid   : ${m['costs_paid']:.2f}  (turnover {m['notional_turnover_x']}x cash)")
     print(f"risk         : max DD {m['max_drawdown_pct']:.2f}%, max exposure {m['max_exposure_pct_observed']:.1f}%, "
diff --git a/src/entropy/bot/strategies/consensus.py b/src/entropy/bot/strategies/consensus.py
index fa7aefd..aca6f3b 100644
--- a/src/entropy/bot/strategies/consensus.py
+++ b/src/entropy/bot/strategies/consensus.py
@@ -401,6 +401,11 @@ class ConsensusStrategy:
         st.direction = 0
         st.bars_in_trade = 0
         st.bars_since_exit = 0
+        # Reset the trail anchors too: a mechanical stop/take-profit must not
+        # leave the next trade inheriting this trade's high-water mark, or the
+        # trail band fires far too early on re-entry.
+        st.peak = 0.0
+        st.last_close = 0.0
 
     # ---- hot path -------------------------------------------------------
 
@@ -551,20 +556,31 @@ class ConsensusStrategy:
             st.streak_dir = 0
             st.direction = sgn
             st.bars_in_trade = 0
+            # Fresh trail anchors: without this the new trade inherits the
+            # PREVIOUS trade's high-water mark (a second long keeps the old
+            # peak, a second short the old low) and the trail band fires far
+            # too early — often on the first bar after min_hold.
+            st.peak = 0.0
+            st.last_close = 0.0
             action = SignalAction.ENTER_LONG if sgn > 0 else SignalAction.ENTER_SHORT
             return [Signal(symbol=symbol, action=action, strength=abs(score),
                            reason=reason, ts_ns=ts_ns, strategy=self.name)]
 
-        if st.bars_in_trade < self.min_hold_bars:
-            return []  # a fresh position rides out its first few bars
-        if self.costs is not None:
-            self._last_move_rms[symbol] = self._bar_move_rms(closes)
+        # Trail anchors accumulate from the FIRST completed bar of the trade:
+        # this block deliberately runs above the min_hold early-return so the
+        # first trail comparison after min_hold uses the true high-water mark
+        # of the whole trade, not just the bars since min_hold elapsed. The
+        # min_hold gate itself only blocks EXIT DECISIONS below.
         close = closes[-1]
         st.last_close = close
         if st.direction > 0:
             st.peak = max(st.peak, close) if st.peak > 0.0 else close
         else:
             st.peak = min(st.peak, close) if st.peak > 0.0 else close
+        if st.bars_in_trade < self.min_hold_bars:
+            return []  # a fresh position rides out its first few bars
+        if self.costs is not None:
+            self._last_move_rms[symbol] = self._bar_move_rms(closes)
         if not self._should_exit(score, votes, st.direction, symbol):
             return []
         st.direction = 0
diff --git a/tests/bot/test_accuracy_harness.py b/tests/bot/test_accuracy_harness.py
new file mode 100644
index 0000000..9f78ad8
--- /dev/null
+++ b/tests/bot/test_accuracy_harness.py
@@ -0,0 +1,289 @@
+"""Honest-measurement integrity of the accuracy harness
+(scripts/entropy_accuracy_btc15m.py).
+
+The harness feeds kline-shaped ticks through the final-state BotRunner, so
+these tests pin the measurement conventions on deterministic synthetic klines
+(a clean 0.3%/bar ramp enters longs reliably; the first entry always fires at
+the roll into bar 36, i.e. confirm_bars=2 both qualifying bars; risk barriers
+are fixed percentages of the slippage-adjusted entry fill because
+vol_window_s=30 leaves fewer than 5 window ticks at entry):
+
+* pessimistic intrabar barrier resolution — the LOW tick is fed before the
+  HIGH tick, so when one bar's range contains both an open position's stop and
+  its take-profit the STOP resolves first;
+* level fills — mechanical stop/TP exit fills are clamped, when pairing, to
+  the barrier level anchored at entry (worse of level and actual fill, both
+  with the close-side adverse slippage), never left at the bar extreme;
+* zero PnL is a loss;
+* long-only reporting;
+* warmup chaining (--warmup-bars).
+
+The script lives in scripts/ (not a package) and is imported via importlib.
+"""
+
+from __future__ import annotations
+
+import importlib.util
+import sys
+from datetime import datetime, timezone
+from pathlib import Path
+from typing import Any
+
+import pytest
+
+_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "entropy_accuracy_btc15m.py"
+_spec = importlib.util.spec_from_file_location("entropy_accuracy_btc15m", _SCRIPT)
+assert _spec is not None and _spec.loader is not None
+mod = importlib.util.module_from_spec(_spec)
+sys.modules[_spec.name] = mod
+_spec.loader.exec_module(mod)
+
+from entropy.bot.config import (  # noqa: E402
+    BotConfig,
+    ConsensusConfig,
+    MarketCostConfig,
+    RiskOverrides,
+)
+
+SYMBOL = mod.SYMBOL
+BAR_MS = mod.BAR_MS
+T0_MS = 1_700_000_000_000  # fixed epoch so timestamps are deterministic
+SLIP = 0.0003              # binance-spot slippage, 3 bps
+
+
+def _kline(i: int, o: float, c: float, hi: float | None = None,
+           lo: float | None = None) -> list[Any]:
+    hi = max(o, c) * 1.002 if hi is None else hi
+    lo = min(o, c) * 0.998 if lo is None else lo
+    return [T0_MS + i * BAR_MS, o, hi, lo, c, 1.0,
+            T0_MS + i * BAR_MS + BAR_MS - 1, 0, 0, 0, 0, 0]
+
+
+def _ramp(n: int, start: float = 100.0, pct: float = 1.003) -> tuple[list[list[Any]], float]:
+    """n up-bars; returns (klines, last close)."""
+    klines: list[list[Any]] = []
+    px = start
+    for i in range(n):
+        o, c = px, px * pct
+        klines.append(_kline(i, o, c))
+        px = c
+    return klines, px
+
+
+def _flat(klines: list[list[Any]], start_i: int, n: int, px: float) -> None:
+    """Append n doji bars pinned at one price (no wicks, no movement)."""
+    for i in range(start_i, start_i + n):
+        klines.append(_kline(i, px, px, hi=px, lo=px))
+
+
+def _base_klines() -> list[list[Any]]:
+    """A 0.3%/bar ramp where bar 38 is a wild bar whose [low, high] swallows
+    the barriers of the position opened at bar 36 (the entry fires at the roll
+    into bar 36; with tp=1.2% the natural take-profit would only trip at bar
+    39's high tick, so at bar 38 the position is necessarily still open)."""
+    klines, px = _ramp(38)          # bars 0..37
+    wild = _kline(38, px, px * 1.004, hi=px * 1.05, lo=px * 0.95)
+    klines.append(wild)
+    px = px * 1.004
+    for i in range(39, 90):
+        o, c = px, px * 1.003
+        klines.append(_kline(i, o, c))
+        px = c
+    return klines
+
+
+def _cfg(fee_bps: float = 10.0, slip_bps: float = 3.0,
+         sl_pct: float = 1.5, tp_pct: float = 1.2) -> BotConfig:
+    return BotConfig(
+        mode="paper", starting_cash=100.0, strategies=("consensus",),
+        symbols=(SYMBOL,), ema_symbol=SYMBOL, ema_fast=9, ema_slow=21,
+        momentum_min_pct=0.15, timeframe="15m", bar_s=900.0, warmup=False,
+        market_costs=MarketCostConfig(
+            crypto_spot_fee_bps=fee_bps, crypto_spot_slippage_bps=slip_bps,
+        ),
+        cost_aware=True, cost_edge_mult=1.0,
+        consensus=ConsensusConfig(
+            exit_mode="hold", min_hold_bars=0, move_floor=0.0003,
+            confirm_bars=2, cooldown_bars=2,
+        ),
+        risk_overrides=RiskOverrides(
+            stop_loss_pct=sl_pct, take_profit_pct=tp_pct, vol_window_s=30.0,
+        ),
+        console_log_path="/tmp/entropy_harness_test/console.log",
+        trade_csv_path="/tmp/entropy_harness_test/trades.csv",
+    )
+
+
+def _iso(ms: float) -> str:
+    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
+
+
+def _run(klines: list[list[Any]], cfg: BotConfig, **kw: Any) -> dict[str, Any]:
+    return mod.simulate(klines, cfg, run_dir="/tmp/entropy_harness_test/ledger", **kw)
+
+
+# ---- the tick order itself ----------------------------------------------------
+
+
+def test_build_ticks_feeds_low_before_high():
+    """Pessimistic intrabar convention, at the unit level: one kline becomes
+    O, L, H, C ticks (stop-tripping tick before TP-tripping tick)."""
+    k = _kline(0, 100.0, 101.0, hi=101.5, lo=99.5)
+    ticks = mod.build_ticks([k])
+    assert [t["price"] for t in ticks] == [100.0, 99.5, 101.5, 101.0]
+    # all four ticks stay inside the same 15m strategy bar
+    bar = ticks[0]["ts_ns"] // int(900 * 1e9)
+    assert all(t["ts_ns"] // int(900 * 1e9) == bar for t in ticks)
+
+
+# ---- both-in-bar resolves stop first ------------------------------------------
+
+
+def test_both_in_bar_resolves_stop_first():
+    """A bar whose [low, high] contains both an open long's stop and its
+    take-profit must close the position via the STOP, not the take-profit.
+
+    Under the legacy O,H,L,C tick order the high tick hit the TP first and
+    every such bar was scored a win — the inflation this convention removes."""
+    klines = _base_klines()
+    report = _run(klines, _cfg())
+    trades = report["trades"]
+    assert trades, "the ramp must produce trades"
+    stops = [t for t in trades if t["exit_intent"] == "stop"]
+    assert len(stops) == 1, f"exactly the wild bar may stop out: {report['exit_breakdown']}"
+    stop = stops[0]
+    assert stop["side"] == "LONG"
+    # both barriers were inside the wild bar's range -> this is the both-in-bar case
+    stop_level = stop["entry_px"] * (1 - 0.015)
+    tp_level = stop["entry_px"] * (1 + 0.012)
+    wild = klines[38]
+    assert wild[3] <= stop_level and wild[2] >= tp_level
+    # the exit fill lands on the wild bar's LOW tick (fed before the H tick)
+    exit_ms = datetime.fromisoformat(stop["exit_ts"]).timestamp() * 1000.0
+    assert wild[0] < exit_ms <= wild[0] + BAR_MS
+    assert exit_ms - wild[0] == 1000.0
+    # and the trade lost money: stop-first is the pessimistic outcome
+    assert stop["pnl"] < 0
+
+
+def test_stop_gap_through_keeps_the_worse_fill():
+    """Worse-of convention: a stop whose bar low is deeper than the barrier
+    keeps the (worse) actual fill instead of being upgraded to the level."""
+    klines = _base_klines()
+    report = _run(klines, _cfg())
+    stop = next(t for t in report["trades"] if t["exit_intent"] == "stop")
+    wild_open = klines[38][1]
+    stop_level = stop["entry_px"] * (1 - 0.015)
+    assert stop_level > wild_open * 0.95  # the low gapped through the barrier
+    assert stop["exit_px"] == pytest.approx(wild_open * 0.95 * (1 - SLIP), rel=1e-3)
+    assert stop["exit_px"] < stop_level * (1 - SLIP)
+
+
+# ---- level fills, not bar extremes ---------------------------------------------
+
+
+def test_take_profit_fill_clamped_to_tp_level_not_bar_high():
+    """A mechanical TP fill is re-priced at the barrier level (minus the
+    close-side adverse slippage), not at the tripping bar's high tick."""
+    klines = _base_klines()
+    report = _run(klines, _cfg())
+    tps = [t for t in report["trades"] if t["exit_intent"] == "take_profit"]
+    assert tps, f"expected TP exits: {report['exit_breakdown']}"
+    for tp in tps:
+        # vol_window_s=30 -> fewer than 5 window ticks at entry -> no volatility
+        # scaling: the TP anchors exactly at entry_fill * (1 + 1.2%)
+        expected = tp["entry_px"] * (1 + 0.012) * (1 - SLIP)
+        assert tp["exit_px"] == pytest.approx(expected, rel=1e-3)
+        # the unclamped fill would have been the tripping bar's HIGH tick minus slip
+        exit_ms = datetime.fromisoformat(tp["exit_ts"]).timestamp() * 1000.0
+        bar_i = round((exit_ms - T0_MS - 2000.0) / BAR_MS)  # H tick sits at +2s
+        assert 0 <= bar_i < len(klines)
+        bar_high = klines[bar_i][2]
+        assert bar_high >= tp["entry_px"] * (1 + 0.012)  # the high reached the level
+        assert tp["exit_px"] < bar_high * (1 - SLIP)
+
+
+def test_strategy_and_liquidation_exits_are_never_clamped():
+    """Only mechanical stop/TP intents are clamped. The end-of-test
+    liquidation (intent 'close') keeps its actual mark-based fill: a position
+    held into a flat tail (price pinned between stop and a far TP) is
+    liquidated exactly at the mark."""
+    klines, last_close = _ramp(46)
+    _flat(klines, 46, 25, last_close)   # between stop (entry*0.985) and TP (entry*10%)
+    report = _run(klines, _cfg(sl_pct=1.5, tp_pct=10.0))
+    closes = [t for t in report["trades"] if t["exit_intent"] == "close"]
+    assert closes, "the final open position must be liquidated as 'close'"
+    for t in closes:
+        # unclamped: liquidation fills at the mark with adverse slippage
+        # (trade records round prices to 2 decimals)
+        assert t["exit_px"] == pytest.approx(last_close * (1 - SLIP), rel=1e-3)
+        # if the TP level (entry*1.10) had been (mis)applied, this would fail
+        assert t["exit_px"] < t["entry_px"] * 1.05
+
+
+# ---- zero PnL is a loss ---------------------------------------------------------
+
+
+def test_zero_pnl_counts_as_a_loss():
+    """With all costs zeroed, a position opened at the ramp/flat boundary and
+    held into a perfectly flat market is liquidated at exactly its entry
+    price: pnl == 0. Only `pnl > 0` is a win, so this trade is a loss."""
+    klines, last_close = _ramp(36)      # bars 0..35
+    _flat(klines, 36, 35, last_close)   # the entry fires at bar 36's open tick
+    report = _run(klines, _cfg(fee_bps=0.0, slip_bps=0.0))
+    assert report["metrics"]["win_definition"] == "pnl > 0"
+    assert report["metrics"]["total_trades"] == 1
+    trade = report["trades"][0]
+    assert trade["exit_intent"] == "close"       # end-of-test liquidation
+    assert trade["pnl"] == 0.0                   # entry price == liquidation mark exactly
+    assert report["metrics"]["win_rate"] == 0.0  # zero PnL is a loss
+
+
+# ---- long-only reporting --------------------------------------------------------
+
+
+def test_long_only_metrics_match_the_long_round_trips():
+    report = _run(_base_klines(), _cfg())
+    trades = report["trades"]
+    longs = [t for t in trades if t["side"] == "LONG"]
+    long_wins = [t for t in longs if t["pnl"] > 0]
+    m = report["metrics"]
+    assert m["total_trades_long_only"] == len(longs)
+    expected = len(long_wins) / len(longs) if longs else 0.0
+    assert m["win_rate_long_only"] == pytest.approx(expected, abs=1e-4)
+    # every trade on this path is long (the spot-deployable subset is everything)
+    assert len(longs) == len(trades)
+    assert m["win_rate_long_only"] == m["win_rate"]
+
+
+# ---- warmup chaining ------------------------------------------------------------
+
+
+def test_warmup_bars_seed_state_without_counting_trades():
+    """--warmup-bars N: the warmup slice feeds strategy/risk state, its trades
+    are not counted, and a position open at the window start is liquidated at
+    the window's first tick (into the warmup ledger, not the report)."""
+    klines = _base_klines()
+    report = _run(klines, _cfg(sl_pct=1.5, tp_pct=10.0), warmup_bars=45)
+    m = report["metrics"]
+    assert report["warmup"]["bars"] == 45
+    # the bar-36 entry is still open (tp 10% never hit on a 4% ramp): the
+    # warmup ledger holds its open fill plus the split liquidation = 1 trade
+    assert report["warmup"]["trades"] == 1
+    window_start = _iso(klines[45][0])
+    assert m["total_trades"] == len(report["trades"])
+    assert report["trades"], "the evaluated window must still trade"
+    for t in report["trades"]:
+        assert t["entry_ts"] >= window_start
+    # the warmup entry must not leak into the evaluated trade list
+    warmup_entry_ts = _iso(klines[36][0])
+    assert all(t["entry_ts"] != warmup_entry_ts for t in report["trades"])
+    assert m["halted"] is False
+
+
+def test_warmup_zero_keeps_legacy_behavior():
+    """warmup_bars=0 must be byte-identical to the legacy cold start."""
+    klines = _base_klines()
+    r_default = _run(klines, _cfg())
+    r_zero = _run(klines, _cfg(), warmup_bars=0)
+    assert r_zero == r_default
diff --git a/tests/bot/test_consensus.py b/tests/bot/test_consensus.py
index 1f720cc..b98733b 100644
--- a/tests/bot/test_consensus.py
+++ b/tests/bot/test_consensus.py
@@ -652,3 +652,118 @@ def test_hold_mode_never_exits_on_score():
     actions = [a for _, a in events]
     assert SignalAction.ENTER_LONG in actions
     assert SignalAction.EXIT not in actions
+
+
+# ---- trail anchor lifecycle (peak/last_close reset) -------------------------
+#
+# The trail band (`px <= peak*(1-trail_pct)` for longs) is only as good as the
+# anchor: a trade must anchor at ITS OWN high-water mark, accumulated from the
+# first completed bar (even while min_hold_bars still gates the exit decision).
+# These scenarios use a two-vote weight map (EMA + RSI) so the MACD histogram —
+# which sours for a dozen bars after any dip — cannot gate the re-entry timing.
+
+
+def _two_long_trades_path() -> tuple[list[float], float]:
+    """Ramp -> entry -> one-bar dip that pierces a 0.5% trail band -> flat
+    bottom -> fresh ramp. The re-entry fires while price is still BELOW the
+    first trade's trail band, with no short in between (the dip is too shallow
+    to qualify one)."""
+    closes: list[float] = []
+    px = 100.0
+    for _ in range(45):
+        px *= 1.003
+        closes.append(px)
+    peak = px
+    for f in (0.994, 0.999, 0.999, 1.0):
+        px *= f
+        closes.append(px)
+    for _ in range(25):
+        px *= 1.003
+        closes.append(px)
+    return closes, peak
+
+
+def test_trail_anchor_resets_on_re_entry():
+    """A second long must anchor its trail at ITS entry region, not the first
+    trade's high-water mark P.
+
+    The re-entry happens below the first trade's trail band, so under the
+    stale-peak bug the very first decision bar of trade 2 would judge
+    ``close <= P*(1-trail_pct)`` true and exit immediately. With the anchor
+    reset at entry, the second trade rides the fresh ramp."""
+    closes, peak = _two_long_trades_path()
+    strat = ConsensusStrategy(
+        symbols=("SPY",), exit_mode="trail", trail_pct=0.005,
+        min_hold_bars=0, cooldown_bars=0, confirm_bars=1,
+        weights={"ema": 0.5, "rsi": 0.5},
+    )
+    events = feed_bars(strat, "SPY", closes)
+    assert [a for _, a in events] == [
+        SignalAction.ENTER_LONG, SignalAction.EXIT, SignalAction.ENTER_LONG,
+    ]
+    _, exit_bar, reentry_bar = (b for b, _ in events)
+    assert exit_bar == reentry_bar - 1  # re-entry on the bar after the trail exit
+    # ... below the first trade's trail band: the bug would exit right here
+    assert closes[reentry_bar] < peak * (1.0 - 0.005)
+    # the flat bottom bar that follows is what the stale anchor would judge —
+    # it is below the band, so the bug's immediate exit triggers exactly here
+    assert closes[reentry_bar + 1] <= peak * (1.0 - 0.005)
+    assert strat._states["SPY"].direction == 1
+
+
+def test_on_position_closed_resets_trail_anchor():
+    """A mechanical stop/take-profit must not leave the stale high-water mark
+    behind for the next trade (the runner re-arms the strategy through this
+    hook when the risk layer closes a position the strategy did not ask to)."""
+    closes = path_trend(3, direction=1, n=60)
+    strat = ConsensusStrategy(
+        symbols=("SPY",), exit_mode="trail", trail_pct=0.005,
+        min_hold_bars=0, confirm_bars=1, weights={"ema": 0.5, "rsi": 0.5},
+    )
+    events = feed_bars(strat, "SPY", closes)
+    assert [a for _, a in events] == [SignalAction.ENTER_LONG]
+    st = strat._states["SPY"]
+    assert st.peak > 0.0 and st.last_close > 0.0  # anchors accumulated
+    strat.on_position_closed("SPY", "stop")
+    assert st.direction == 0
+    assert st.peak == 0.0
+    assert st.last_close == 0.0
+
+
+def test_peak_accumulates_during_min_hold_and_anchors_first_trail_decision():
+    """The trail anchor must accumulate from the FIRST completed bar of the
+    trade, even though min_hold_bars gates the exit decision itself.
+
+    The run-up to the trade's high-water mark happens entirely inside the
+    min_hold window; the crash below the trail band happens while min_hold is
+    still active and price then goes flat. The exit must therefore fire on the
+    VERY FIRST post-min-hold decision bar — which is only possible if the peak
+    includes the pre-min-hold run-up (a peak tracked from min_hold onward would
+    sit at the flat price and never exit)."""
+    closes: list[float] = []
+    px = 100.0
+    for _ in range(35):  # the entry fires on the earliest bar the indicators allow
+        px *= 1.003
+        closes.append(px)
+    px *= 1.01  # trade bar 1: run-up to the trade high while min_hold is active
+    trade_high = px
+    closes.append(px)
+    px *= 0.95  # trade bar 2: crash below the trail band, still inside min_hold
+    closes.append(px)
+    for _ in range(15):  # flat below the band for the rest of the path
+        px *= 1.0005
+        closes.append(px)
+
+    strat = ConsensusStrategy(
+        symbols=("SPY",), exit_mode="trail", trail_pct=0.02,
+        min_hold_bars=5, cooldown_bars=0, confirm_bars=1,
+        weights={"ema": 0.5, "rsi": 0.5},
+    )
+    events = feed_bars(strat, "SPY", closes)
+    assert [(b, a) for b, a in events] == [
+        (35, SignalAction.ENTER_LONG), (40, SignalAction.EXIT),
+    ]
+    entry_bar, exit_bar = (b for b, _ in events)
+    assert exit_bar - entry_bar == 5  # the FIRST decision bar min_hold allows
+    # the anchor was the pre-crash high accumulated during min_hold
+    assert strat._states["SPY"].peak == pytest.approx(trade_high)
