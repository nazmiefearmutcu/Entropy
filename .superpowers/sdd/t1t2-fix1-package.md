# commits
c313e81 fix(scripts): direction-aware intrabar barrier order — shorts resolve stop first too

# stat
 scripts/entropy_accuracy_btc15m.py | 89 ++++++++++++++++++++++++++++----------
 tests/bot/test_accuracy_harness.py | 55 +++++++++++++++++++++--
 2 files changed, 119 insertions(+), 25 deletions(-)

# full diff (round-1 fix only)
diff --git a/scripts/entropy_accuracy_btc15m.py b/scripts/entropy_accuracy_btc15m.py
index d932e4e..2afcbe5 100644
--- a/scripts/entropy_accuracy_btc15m.py
+++ b/scripts/entropy_accuracy_btc15m.py
@@ -18,12 +18,14 @@ Usage:
 Honest-measurement conventions (these intentionally lower the measured win
 rate versus the original harness — that is the point):
 
-  * Pessimistic intrabar barrier resolution. Each 15m bar is fed as 4 ticks
-    O, L, H, C — the stop-tripping tick BEFORE the take-profit-tripping tick.
-    When a bar's range contains both an open position's stop and its
-    take-profit, the STOP resolves first (a same-bar entry whose first bar
-    spans both barriers is covered too, which a per-bar pre-scan of
-    already-open positions would miss).
+  * Pessimistic intrabar barrier resolution. Each 15m bar is fed as 4 ticks:
+    the open, then the low/high pair in the DIRECTION-AWARE pessimistic order
+    (an open long feeds L before H, an open short feeds H before L; the order
+    is chosen after the open tick so just-opened positions are covered), then
+    the close. Whenever a bar's range contains both an open position's stop
+    and its take-profit, the STOP resolves first — for either direction (a
+    fixed order is only pessimistic for longs; L-before-H would resolve the
+    TP first for shorts).
   * Level fills, not bar extremes. Exit fills from mechanical stop/TP intents
     are clamped, when pairing, to the barrier level the risk layer anchored at
     entry (worse of level and actual fill, both with the close-side adverse
@@ -128,24 +130,22 @@ def fetch_klines(bars: int, end_ms: int, cache: Path | None = None,
 
 
 def build_ticks(klines: list[list[Any]], symbol: str = SYMBOL) -> list[dict[str, Any]]:
-    """One 15m kline -> 4 ticks: O, L, H, C.
-
-    The LOW tick is fed BEFORE the HIGH tick on purpose (pessimistic intrabar
-    convention): the risk layer compares the running mark against an open
-    position's stop and take-profit on every tick, so with the historical
-    O,H,L,C order a take-profit inside the bar always resolved BEFORE a stop
-    also inside the bar, at the bar's extreme price. Feeding L first makes the
-    STOP resolve first whenever both barriers sit inside one bar's [low, high]
-    — including bars where the position was opened at the open tick — and the
-    remaining level-fill clamp in simulate() keeps mechanical fills honest.
-    Strategy bars bucket on ts//900s so all four ticks land in the same 15m
-    bar regardless of order.
+    """One 15m kline -> 4 ticks in canonical order O, L, H, C, grouped 4 per
+    bar (simulate() re-stamps and swaps the L/H pair per bar by open-position
+    DIRECTION so the STOP always resolves before the TP inside a bar: L first
+    for longs, H first for shorts — see BotRunner feed in simulate).
+
+    With the historical O,H,L,C order a take-profit inside the bar always
+    resolved BEFORE a stop also inside the bar, at the bar's extreme price.
+    The remaining level-fill clamp in simulate() keeps mechanical fills
+    honest. Strategy bars bucket on ts//900s so all four ticks land in the
+    same 15m bar regardless of order.
     """
     ticks: list[dict[str, Any]] = []
     for k in klines:
         open_ms = int(k[0])
         open_px, high, low, close = float(k[1]), float(k[2]), float(k[3]), float(k[4])
-        # open, low, high, close as 4 ticks inside the bar (stop before TP)
+        # open, low, high, close (direction-aware stop-first ordering in feed)
         for off_s, px in ((0, open_px), (1, low), (2, high), (3, close)):
             ticks.append({
                 "symbol": symbol, "price": px, "amount": 1.0, "side": "buy",
@@ -169,6 +169,11 @@ class _BarrierLedger(DummyLedger):
     from the live position at open-fill time — after ``portfolio.open`` — so
     the anchoring math is mirrored exactly, and the pairing step in
     :func:`simulate` can clamp mechanical exit fills back to the barrier.
+
+    INVARIANT: barrier levels are immutable after open. The risk layer has no
+    trailing stop and never re-anchors stop/TP while a position is open, which
+    is the only reason capturing once at open is valid — if re-anchoring or a
+    risk-layer trail is ever added, this capture must move to per-exit time.
     """
 
     def __init__(self, runner: BotRunner) -> None:
@@ -246,11 +251,28 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
     eval_ticks = build_ticks(klines[warmup_bars:], symbol=symbol)
 
     def _feed(ticks: list[dict[str, Any]]):
+        """Feed ticks bar by bar (build_ticks yields exactly 4 per bar: O,
+        L, H, C). The L/H order is decided PER BAR, after the open tick, from
+        the direction of the position open at that moment — a bar-roll entry
+        can only fire on the O tick (strategy evaluation runs on bucket rolls
+        only), so checking after the O tick covers just-opened positions too:
+
+          long  -> L before H: the stop (below) tick trips before the TP tick
+          short -> H before L: the stop (above) tick trips before the TP tick
+          flat  -> L, H, C    (no barriers; the order is neutral)
+
+        A fixed order cannot be pessimistic for both directions: L-before-H
+        resolves the take-profit FIRST for shorts whose stop and TP both sit
+        inside one bar — the mirror image of the long-side O,H,L,C bias this
+        harness exists to remove. Tick timestamps are re-stamped on swap so
+        the fed time stays monotonic."""
         curve: list[tuple[int, float, float]] = []
         max_open = 0
         max_exp = 0.0
         prev_day = None
-        for t in ticks:
+
+        def _on(t: dict[str, Any]) -> None:
+            nonlocal max_open, max_exp, prev_day
             day = utc_day(t["ts_ns"])
             if day != prev_day:
                 # Backtest is fast-forwarded, so the runner's wall-clock day
@@ -269,6 +291,22 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
             exp = runner.portfolio.exposure()
             if snap.equity > 0 and exp / snap.equity > max_exp:
                 max_exp = exp / snap.equity
+
+        for i in range(0, len(ticks), 4):
+            bar = ticks[i:i + 4]
+            _on(bar[0])
+            pos = runner.portfolio.positions.get(bar[0]["symbol"])
+            rest = bar[1:]
+            if pos is not None and pos.side is PositionSide.SHORT and len(rest) == 3:
+                # short: feed the HIGH tick (stop side) before the LOW tick
+                # (TP side); re-stamp the swapped ticks' timestamps so time
+                # stays monotonic within the bar (ticks are spaced 1s apart)
+                h, low = dict(bar[2]), dict(bar[1])
+                h["ts_ns"] = bar[0]["ts_ns"] + _NS
+                low["ts_ns"] = bar[0]["ts_ns"] + 2 * _NS
+                rest = [h, low, bar[3]]
+            for t in rest:
+                _on(t)
         return curve, max_open, max_exp
 
     warmup_trades = 0
@@ -301,6 +339,9 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
         resolved_costs.slippage_bps if resolved_costs.slippage_bps is not None
         else cfg.slippage_bps
     )
+    fee_bps_close = (
+        resolved_costs.fee_bps if resolved_costs.fee_bps is not None else cfg.fee_bps
+    )
     wins = losses = 0
     long_trades = long_wins = 0
     total_profit = total_loss = 0.0
@@ -318,6 +359,7 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
         else:
             entry = positions_history.pop(fill.symbol)
             exit_px = fill.price
+            close_fee = fill.fee
             levels = trade_levels.pop(fill.symbol, None)
             if intent.value in ("stop", "take_profit") and levels is not None:
                 # Level fills, not bar extremes: clamp mechanical stop/TP exit
@@ -331,12 +373,15 @@ def simulate(klines: list[list[Any]], cfg: BotConfig,
                     exit_px = min(level * (1.0 - slip_bps / 10_000.0), fill.price)
                 else:                          # closing a short: worse = higher
                     exit_px = max(level * (1.0 + slip_bps / 10_000.0), fill.price)
+                # the recorded fee was charged on the actual (extreme) fill
+                # notional; re-price it at the clamped exit price
+                close_fee = abs(exit_px * fill.qty) * (fee_bps_close / 10_000.0)
             qty = fill.qty
             side_str = "LONG" if entry.side.value == "buy" else "SHORT"
             if side_str == "LONG":
-                pnl = (exit_px - entry.price) * qty - entry.fee - fill.fee
+                pnl = (exit_px - entry.price) * qty - entry.fee - close_fee
             else:
-                pnl = (entry.price - exit_px) * qty - entry.fee - fill.fee
+                pnl = (entry.price - exit_px) * qty - entry.fee - close_fee
             closed_pnls.append(pnl)
             trades.append({
                 "side": side_str,
diff --git a/tests/bot/test_accuracy_harness.py b/tests/bot/test_accuracy_harness.py
index 9f78ad8..9062a5e 100644
--- a/tests/bot/test_accuracy_harness.py
+++ b/tests/bot/test_accuracy_harness.py
@@ -8,9 +8,12 @@ the roll into bar 36, i.e. confirm_bars=2 both qualifying bars; risk barriers
 are fixed percentages of the slippage-adjusted entry fill because
 vol_window_s=30 leaves fewer than 5 window ticks at entry):
 
-* pessimistic intrabar barrier resolution — the LOW tick is fed before the
-  HIGH tick, so when one bar's range contains both an open position's stop and
-  its take-profit the STOP resolves first;
+* pessimistic intrabar barrier resolution — the low/high tick order inside a
+  bar is DIRECTION-AWARE (an open long feeds L before H, an open short feeds
+  H before L; the order is chosen after the open tick, so just-opened
+  positions are covered): when one bar's range contains both an open
+  position's stop and its take-profit the STOP resolves first, for either
+  direction;
 * level fills — mechanical stop/TP exit fills are clamped, when pairing, to
   the barrier level anchored at entry (worse of level and actual fill, both
   with the close-side adverse slippage), never left at the bar extreme;
@@ -179,6 +182,52 @@ def test_stop_gap_through_keeps_the_worse_fill():
     assert stop["exit_px"] < stop_level * (1 - SLIP)
 
 
+def _short_base_klines() -> list[list[Any]]:
+    """Mirror of :func:`_base_klines` on a 0.3%/bar downtrend: the short entry
+    fires at the roll into bar 36 and bar 38 is a wild bar whose [low, high]
+    swallows the short's stop (above) AND take-profit (below)."""
+    klines, px = _ramp(38, pct=0.997)   # bars 0..37
+    klines.append(_kline(38, px, px * 0.996, hi=px * 1.05, lo=px * 0.95))
+    px *= 0.996
+    for i in range(39, 90):
+        o, c = px, px * 0.997
+        klines.append(_kline(i, o, c))
+        px = c
+    return klines
+
+
+def test_both_in_bar_resolves_stop_first_for_shorts():
+    """The mirror case: for a SHORT the stop sits ABOVE the entry and the
+    take-profit below, so a fixed L-before-H order would resolve the TP first
+    (the direction bias the round-1 review caught — it reintroduced the WR
+    inflation for shorts). The wild bar must stop the position out via its
+    HIGH tick, which for shorts is fed before the low tick."""
+    klines = _short_base_klines()
+    report = _run(klines, _cfg())
+    trades = report["trades"]
+    assert trades, "the downtrend must produce trades"
+    shorts = [t for t in trades if t["side"] == "SHORT"]
+    assert shorts, "the downtrend must produce SHORT trades"
+    stops = [t for t in trades if t["exit_intent"] == "stop"]
+    assert len(stops) == 1, f"exactly the wild bar may stop out: {report['exit_breakdown']}"
+    stop = stops[0]
+    assert stop["side"] == "SHORT"
+    # both barriers were inside the wild bar's range -> the both-in-bar case
+    stop_level = stop["entry_px"] * (1 + 0.015)
+    tp_level = stop["entry_px"] * (1 - 0.012)
+    wild = klines[38]
+    assert wild[2] >= stop_level and wild[3] <= tp_level
+    # the exit fill lands on the wild bar's HIGH tick — fed FIRST for shorts
+    # (re-stamped to +1s so fed time stays monotonic)
+    exit_ms = datetime.fromisoformat(stop["exit_ts"]).timestamp() * 1000.0
+    assert wild[0] < exit_ms <= wild[0] + BAR_MS
+    assert exit_ms - wild[0] == 1000.0
+    # worse-of keeps the deeper BUY fill (bar high + adverse slippage)
+    assert stop["exit_px"] == pytest.approx(wild[2] * (1 + SLIP), rel=1e-3)
+    assert stop["exit_px"] > stop_level * (1 + SLIP)
+    assert stop["pnl"] < 0
+
+
 # ---- level fills, not bar extremes ---------------------------------------------
 
 
