# T1+T2 Report — honest-measurement fixes (Wave 1)

**Status: DONE** (with two documented deviations from the brief's letter, both below)
**Date:** 2026-09-03
**Base HEAD at start:** `a7a7c1fbf1e592537915fbc8f59772df6ac674b7`
**Commits:**
- `9aee08e` fix(bot): reset consensus trail peak on entry and close, track it through min_hold
- `877d88c` fix(scripts): pessimistic intrabar barrier resolution + level fills in accuracy harness

**Test summary:** `.venv/Scripts/python -m pytest tests/bot tests/strategy -q` → exit 0,
287 collected (276 pre-existing + 3 new consensus + 8 new harness), zero failures.
All 11 new tests were verified to FAIL on the pre-fix code (git stash of the
changed source file, run, pop).

## T1 — consensus trail peak reset (src/entropy/bot/strategies/consensus.py)

1. **Entry reset:** in `_evaluate`, where `st.direction = sgn` is set, added
   `st.peak = 0.0; st.last_close = 0.0` (with comment). A second long no longer
   inherits the previous long's high-water mark; shorts mirrored.
2. **`on_position_closed` reset:** same two resets added (plus comment) after the
   existing bars_in_trade/bars_since_exit reset.
3. **Tracking above min_hold:** the peak/last_close update block moved ABOVE the
   `bars_in_trade < min_hold_bars` early-return; the gate now only blocks exit
   DECISIONS. The cost-model `_last_move_rms` update deliberately stayed below
   the gate (unchanged behavior for score-based exits' cost buffer).
4. `_should_exit` "trail" branch untouched.

Tests (tests/bot/test_consensus.py, +3, all deterministic synthetic paths; a
two-vote weight map `{"ema": 0.5, "rsi": 0.5}` is used so the MACD histogram —
which sours for ~12 bars after any dip — cannot gate re-entry timing):
- `test_trail_anchor_resets_on_re_entry`: ramp → 0.5%-trail exit → one-bar dip
  below the old band → flat bottom → fresh ramp. Pinned sequence
  [ENTER_LONG, EXIT, ENTER_LONG]; re-entry bar is below the first trade's trail
  band and the flat bottom bar is what the stale anchor would judge — under the
  bug the sequence would be [ENTER_LONG, EXIT, ENTER_LONG, EXIT].
- `test_on_position_closed_resets_trail_anchor`: after a real entry with
  accumulated anchors, the hook clears peak/last_close/direction.
- `test_peak_accumulates_during_min_hold_and_anchors_first_trail_decision`:
  run-up + crash both inside min_hold=5, flat thereafter; exit fires exactly at
  exit_index − entry_index == 5 (the FIRST post-min-hold decision bar), and
  `st.peak` equals the pre-crash high — impossible if the anchor only tracked
  post-min-hold bars.

## T2 — harness integrity (scripts/entropy_accuracy_btc15m.py)

1. **Pessimistic intrabar convention:** `build_ticks` now feeds each bar as
   **O, L, H, C** (stop-tripping tick before TP-tripping tick), documented in
   the docstring. Deviation from the brief's letter (see Deviations): the brief
   suggested a per-bar pre-scan reordering only when an already-open position's
   barriers are both in-range; the unconditional reorder is outcome-identical
   for every case the pre-scan covers AND additionally covers the common
   same-bar-entry case (every entry's first bar starts with the position open
   at the O tick, invisible to a bar-start pre-scan). Verified the reorder
   changes nothing else: strategy bar closes are still the C tick (last tick in
   bucket), entry evaluations still happen at O ticks, and the risk layer's
   vol-window stats see the same price set.
2. **Level fills:** new `_BarrierLedger(DummyLedger)` captures, at record time
   of each OPEN fill, the `(stop_px, tp_px)` the runner anchored via
   `RiskManager.stop_tp_prices(fill.price, symbol)` — read from the live
   `PositionState` after `portfolio.open`, so the anchoring math (slippage-
   adjusted entry fill + volatility scale factor) is mirrored exactly rather
   than recomputed. Levels are stored in a list aligned 1:1 with `fills`
   (a symbol-keyed dict was tried first and is WRONG: it is overwritten by
   later trades before the pairing walk reads it). In the pairing loop, exit
   fills with intent `stop`/`take_profit` are re-priced to the barrier level
   with the executor's close-side adverse slippage (`PaperExecutor` math:
   `slip = level * slip_bps/1e4`, adverse direction), then the WORSE of
   (level_fill, actual fill) is used: `min` for SELL closes (longs), `max` for
   BUY closes (shorts). Intent `close` (strategy exits, end-of-test and
   warmup-boundary liquidation) is never clamped. Intents verified against
   `OrderIntent` (`"stop"`, `"take_profit"`, `"close"`) and
   `RiskManager.check_exits`.
3. **Zero PnL is a loss:** the tally was already `pnl > 0`-gated; made explicit
   with a comment and reported as `metrics["win_definition"] = "pnl > 0"`.
4. **Warmup chaining:** `simulate(..., warmup_bars=0)` splits the klines
   (clamped to leave ≥ 1 evaluated bar). Warmup ticks feed the same runner
   (strategy/risk state carries; day-rollover logic shared) into a separate
   warm `_BarrierLedger`; positions open at the window start are liquidated at
   the evaluated window's first tick via the extracted
   `_liquidate_open_positions` helper (same liquidation math as end-of-test,
   factored out), recorded into the warm ledger, and the strategies are
   re-armed via `runner._notify_closed(sym, "warmup_liquidation")` so the
   post-split run can actually trade. Eval ledger swaps in afterwards:
   evaluated metrics pair only eval fills (entries ≥ window start). Equity
   curve/daily returns/maxDD are recorded for evaluated ticks only. CLI:
   `--warmup-bars N` (default **100**); `main()` fetches `bars + warmup` and
   reports the evaluated window in `config.start_utc/bars` plus
   `config.warmup_bars` and `report["warmup"] = {"bars", "trades"}`.
   `warmup_bars=0` is byte-identical to the legacy path (pinned by test).
5. **Long-only reporting:** `win_rate_long_only` + `total_trades_long_only`
   over LONG round trips; printed in the human-readable block.

**sweep / walk-forward:** both scripts import `build_ticks` + `simulate` from
the accuracy script (verified), so fixes 1–3 (and the new `simulate` default)
are inherited automatically; NO edits made to either file. Import check:
`python -c "import entropy_wr_sweep, entropy_walk_forward"` OK.

Tests (tests/bot/test_accuracy_harness.py, NEW, 9 tests; script imported via
`importlib.util.spec_from_file_location`): tick order unit test; both-in-bar
resolves stop first (exit on the wild bar's L tick at +1s, both barriers proven
inside the bar range, pnl < 0); stop gap-through keeps the worse (deeper) fill;
TP fill clamped exactly to `entry*(1+tp%)*(1-slip)` and strictly below the
tripping bar's high fill; liquidation `close` fills untouched (mark ± slip);
zero-PnL trade (costs zeroed, flat tail) is one trade with `pnl == 0.0` counted
as a loss, `win_rate == 0.0`; long-only metrics recomputed from the trade list;
warmup 45 (warmup trades counted in `report["warmup"]`, no warmup entry leaks
into eval, all eval entries ≥ window start); `warmup_bars=0` byte-identical to
the no-arg call.

## Deviations from the brief (both intentional, documented in code)

1. **Unconditional O,L,H,C instead of a conditional pre-scan reorder** —
   strictly more pessimistic and identical everywhere the conditional applies;
   the conditional variant misses both-in-bar bars for positions opened at that
   bar's open tick (i.e. every trade's first bar).
2. **Barrier levels captured from the live position at open-fill time instead
   of recomputed from entry fill + configured percents** — the recomputation
   cannot reproduce `stop_tp_prices`' volatility scale factor (it depends on
   the tick window at entry time); capturing the anchored levels mirrors the
   math exactly by construction. The brief itself says "mirror that math
   exactly".

## Notes / minor findings

- First implementation attempt used a symbol-keyed barrier dict read during the
  pairing walk — wrong because the run completes before the walk (dict holds
  only the LAST trade's levels; clamp silently no-ops for earlier trades).
  Caught by the smoke test; fixed with the record-time aligned list.
- Trade records round prices to 2 decimals; tests assert with rel=1e-3.
- The repo's pytest prints no "N passed" summary line under `-q` (pre-existing
  quirk, likely config/plugin related); the gate is judged by exit code 0 and
  zero F/E marks — 287/287 green.
- `--bars` CLI help now says "EVALUATED 15m bars (warmup bars are extra)";
  existing flags otherwise unchanged and backward compatible.
- Files touched: exactly `src/entropy/bot/strategies/consensus.py`,
  `tests/bot/test_consensus.py`, `scripts/entropy_accuracy_btc15m.py`,
  `tests/bot/test_accuracy_harness.py` (new). UI tests untouched.

---

# Round 1 — review fixes (direction-aware intrabar order)

**Status: DONE**
**Commit:** `c313e81` fix(scripts): direction-aware intrabar barrier order — shorts resolve stop first too
**Test summary:** `pytest tests/bot tests/strategy -q` exit 0 — 288 collected (287 + 1 new short-side test), zero failures. The new short test fails on the round-1 code and passes with the fix (verified via git stash).

## CRITICAL fixed — direction bias in the O,L,H,C reorder

The round-1 unconditional L-before-H order was pessimistic only for LONGS. For
a SHORT the barriers mirror (stop above the entry → tripped by the HIGH tick;
take-profit below → tripped by the LOW tick), so L-before-H resolved the TP
first — the reviewer empirically confirmed the exact WR inflation the
convention exists to remove, mirrored to shorts (short @89.72, wild bar
[79.81, 97.54] exited `take_profit` at the L tick; legacy O,H,L,C would have
tripped the stop at H for a loss).

Fix (scripts/entropy_accuracy_btc15m.py, `simulate()._feed`): the feed is now
bar-centric (build_ticks yields exactly 4 ticks per bar: O, L, H, C). After
feeding the OPEN tick, the direction of the position open at that moment picks
the order of the remaining three:

- long open → L, H, C (stop-side low tick first)
- short open → H, L, C (stop-side high tick first; the swapped ticks are
  re-stamped to +1s/+2s so fed time stays monotonic)
- flat → L, H, C (no barriers; order is neutral)

The just-opened-at-O case is covered by construction: strategy evaluation only
runs on bucket rolls, i.e. on a bar's OPEN tick, so no entry can fire mid-bar —
checking the portfolio after the O tick sees every position that can be open
for the rest of the bar, including same-tick reversal entries (EXIT +
opposite ENTER on one tick). The risk layer is still untouched.

Module docstring, `build_ticks` docstring and the harness test-file docstring
updated to describe the direction-aware convention.

## IMPORTANT fixed — short-side regression test

`tests/bot/test_accuracy_harness.py::test_both_in_bar_resolves_stop_first_for_shorts`
(+ helper `_short_base_klines`, a 0.3%/bar downtrend mirror of the long path):
short entry at bar 36, wild bar at 38 with hi = lo-spanning ±5% around the
open. Asserts exactly one stop exit; both barriers proven inside the wild
bar's [low, high]; the exit fill lands on the wild bar's HIGH tick (+1s);
worse-of keeps the deeper BUY fill (`wild_high*(1+slip)` > stop level fill);
pnl < 0. Fails on the round-1 code (TP resolved first), passes with the fix.

## MINOR fixed

1. **Clamped-exit fee re-priced:** when a mechanical stop/TP fill is clamped
   to the barrier level, the close fee is recomputed at the clamped price
   (`abs(exit_px * qty) * fee_bps/1e4`, resolved per-symbol like the
   slippage) instead of keeping the fee charged on the extreme-fill notional.
   Strategy exits and the liquidation (intent "close") keep their actual
   recorded fees.
2. **`_BarrierLedger` invariant documented:** capture-at-open is valid only
   because barrier levels are immutable after open (the risk layer has no
   trailing stop and never re-anchors stop/TP); if re-anchoring or a
   risk-layer trail is ever added, the capture must move to per-exit time.

## Files touched in round 1

Exactly `scripts/entropy_accuracy_btc15m.py` and
`tests/bot/test_accuracy_harness.py`. UI tests untouched. No push.
