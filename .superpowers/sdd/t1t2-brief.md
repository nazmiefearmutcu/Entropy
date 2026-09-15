# T1+T2 Brief — honest-measurement fixes (batched, disjoint files)

Repo: C:\Users\Kullanıcı\Entropy (Python, src layout, pytest). Venv: `.venv/Scripts/python`
(Windows). Run tests with `.venv/Scripts/python -m pytest tests/bot tests/strategy -q`.
Design: docs/superpowers/specs/2026-09-03-winrate-over-60-design.md (read it).
These are the two Wave-1 tasks. Touch ONLY the files named here plus their tests.

## T1 — Consensus trail peak reset bug (src/entropy/bot/strategies/consensus.py)

Bug: `_SymbolState.peak` is never reset when a position is opened (entry path in
`_evaluate`, the `st.direction = sgn` branch) nor in `on_position_closed`. For longs the
update is `st.peak = max(st.peak, close) if st.peak > 0.0 else close`, so a second long
trade inherits the PREVIOUS long's high-water mark and the `exit_mode="trail"` band
(`px <= peak*(1-trail_pct)`) fires far too early. Shorts have the mirror bug
(`min(...)` against a stale low). Also, while `bars_in_trade < min_hold_bars` the
method returns BEFORE the peak/last_close update block, so the trail's first comparison
uses a peak from a mismatched window.

Fix (all inside consensus.py, no API change):
1. On entry (where `st.direction = sgn` is set): `st.peak = 0.0`, `st.last_close = 0.0`.
2. In `on_position_closed`: same reset (`st.peak = 0.0`, `st.last_close = 0.0`).
3. Move the peak/last_close tracking ABOVE the `min_hold_bars` early-return so the
   trail anchor accumulates from the first completed bar of the trade. Keep the
   `min_hold_bars` gate for EXIT DECISIONS only (no exit signal before min_hold).
4. `_should_exit` "trail" branch must stay unchanged in behavior when peak is fresh.

Tests (add to tests/bot/test_consensus.py):
- Two consecutive long trades: simulate a rally to peak P, trail exit, re-entry;
  assert the second trade's trail anchor starts from ITS entry region, not P
  (construct bars that would exit immediately under the bug but not after the fix).
- Peak accumulates during min_hold_bars (construct a trade whose first bars run up
  while min_hold is active, then verify the trail uses the accumulated peak).

## T2 — Harness integrity (scripts/entropy_accuracy_btc15m.py ONLY)

Current bias: every 15m bar is fed as 4 ticks O,H,L,C; the risk layer closes at the
current tick mark, so a TP inside the bar always resolves BEFORE an SL also inside the
bar (H precedes L), and fills happen at the bar extreme (bar high for TP, bar low for
SL) instead of the barrier level. This inflates win rate and PF.

Required changes:
1. **Pessimistic intrabar convention:** when both SL and TP lie within one bar's range,
   the STOP must resolve first. Implement WITHOUT touching the risk layer: pre-scan
   each bar's kline in `simulate()` — before feeding the bar's ticks, if an open
   position's stop and take-profit are both reachable inside this bar's [low, high]
   (long: low <= stop_price AND high >= tp_price; short mirrored), feed a synthesized
   tick that trips the stop FIRST (price = stop level), let the risk layer close it,
   then feed the remaining ticks. Simplest robust approach: for each bar, feed the
   O/H/L/C ticks but, when the both-in-bar condition holds for an open position,
   reorder to O,L,H,C for that bar (stop tick before TP tick) AND clamp the fill
   price afterward (see 2). Document the convention in a comment.
2. **Level fills, not bar extremes:** after the run, when pairing fills, clamp exit
   fills that came from mechanical stop/TP intents to the barrier level: recompute
   stop_price/tp_price from the entry fill and the configured stop_loss_pct /
   take_profit_pct (they are configured as percents of entry; the risk layer anchors
   them to the slippage-adjusted entry fill — mirror that math exactly, see
   src/entropy/bot/risk/manager.py and risk/barriers.py). Apply the same adverse
   slippage the executor would charge on the close side. Do not touch winning
   strategy exits (intent "close" or strategy-initiated) — only mechanical
   stop/take_profit intents get clamped toward the worse of (level, actual fill).
3. **Zero PnL is a loss:** in the win/loss tally, `pnl > 0` is a win, everything else
   (including exactly 0) is a loss. Note it in the report JSON as a field
   `"win_definition": "pnl > 0"`.
4. **Warmup chaining:** add `--warmup-bars N` (default 100) that fetches N extra bars
   before the evaluated window and feeds them through the runner WITHOUT counting
   their trades (positions open at window start are liquidated at the window's first
   tick before the evaluated feed begins — use the same liquidation math already in
   the script). Evaluated metrics must only include trades entered at/after the window
   start. Keep default behavior identical when N=0.
5. **Long-only reporting:** add `"win_rate_long_only"` + `"total_trades_long_only"` to
   report metrics computed over LONG round trips only (spot-deployable subset).
   Print it in the human-readable block.

Also update scripts/entropy_wr_sweep.py and scripts/entropy_walk_forward.py ONLY to
inherit 1-3 automatically — they import `simulate` from the accuracy script, so verify
they need no edit; if they replicate the old math inline, apply the same changes.

Tests: add tests/bot/test_accuracy_harness.py covering (a) both-in-bar resolves stop
first, (b) TP fill clamped to TP level (not bar high), (c) zero-PnL counts as loss,
(d) long-only metric correctness, (e) warmup-bars: no trades counted from warmup,
open warmup position liquidated at window start. Import the script via
`sys.path` + `importlib` (it lives in scripts/, not a package).

## Constraints
- Full suite must stay green: `.venv/Scripts/python -m pytest tests/bot tests/strategy -q`
  (UI tests are env-flaky on this Windows box; do not touch them, do not "fix" them).
- No new dependencies. Keep the CLI backward compatible (all existing flags behave the
  same; new flags optional with defaults preserving old behavior EXCEPT the harness
  integrity fixes 1-3, which are the new honest default and intentionally change
  measured numbers).
- Report file: append to .superpowers/sdd/t1t2-report.md with status
  (DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED), list of commits, test
  summary (counts), concerns.

## Report contract
Return in your final message ONLY: status, commit list, one-line test summary,
concerns. Full detail goes in the report file.
