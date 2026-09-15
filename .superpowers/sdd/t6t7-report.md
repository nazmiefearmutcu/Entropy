# T6+T7 report — entry-bar grace (harness) + risk-trail stop (risk layer + harness)

Date: 2026-09-03. Base: b91e644. Status: DONE. Single implementer, no subagents.
Commit: `feat(bot): entry-bar grace + risk-trail stop (Round 2)` (see `git log -1`).

## What changed per mechanism

### T6 — entry-bar grace (`--entry-grace-bars N`, harness only, default 0 = off)

Pure simulator-path change: the LIVE bot's risk layer is untouched. The harness
wraps the runner's per-tick stop/TP resolution so the MECHANICAL stop is not
hit-checked against a position for the entry bar + N-1 following completed
bars. TP stays live; strategy exits unaffected; barrier values never re-anchored.

- `scripts/entropy_accuracy_btc15m.py`:
  - `_grace_exempt(entry_ts_ns, ts_ns, bar_ns, grace_bars)` (module-level
    helper, ~line 168): grace counting. The entry bar (the bar whose O tick
    opened the position) is bar 0, so `(ts - entry_ts) // bar_ns < N` exempts
    the entry bar + N-1 following bars; `N=0` is off.
  - `simulate(..., entry_grace_bars=0)` (~line 256): installs a
    `_check_exits` wrapper on `runner.risk.check_exits` before any feed. The
    wrapper suppresses `OrderIntent.STOP` orders for grace-exempt positions
    (the position is still open at check time, so `pos.entry_ts_ns` is
    available) and lets everything else through unchanged. `grace=0` returns
    the original order list untouched → byte-identical.
  - Report: `report["entry_grace_bars"]` top-level (simulate) + the
    `report.json` config `risk` block and the console levers line (main).
  - `main()`: `--entry-grace-bars` (int, default 0).

### T7 — risk-trail breakeven/profit-slice stop (`RiskOverrides.risk_trail_pct`, default 0.0)

Live-bot-correct: lives in the risk layer, runs on the same per-tick loop as
`check_exits`, BEFORE hit-checking.

- `src/entropy/bot/risk/manager.py`:
  - `RiskManager.__init__(..., risk_trail_pct=0.0)` + `set_risk_trail(pct)`
    (hot-apply), ~line 34.
  - `_ratchet_stop(pos, mark_px)` (~line 330): for `pct > 0` and
    `tp_dist = tp_px - entry_px` (long) / mirror (short) `> 0`:
    trigger = `entry ± pct * tp_dist`; target = `entry ± max(pct - 1.0, 0.0) * tp_dist`.
    On trigger the stop ratchets monotonically toward the TP (long: `max`, short: `min`).
    `0 < pct < 1` → breakeven at entry; `pct >= 1` → profit slice.
  - `check_exits` calls `_ratchet_stop` per position BEFORE the hit checks, so
    the stop level a tick sees is the level after any ratchet that tick
    triggered (the brief's T1T2-M3 ordering requirement).
- `src/entropy/bot/config.py`:
  - `RiskOverrides.risk_trail_pct: float = 0.0` + `__post_init__` ValueError on
    `< 0`; added to `_RISK_BARRIER_FIELDS` so `active()`/`profile()` never hand
    it to `make_custom` (active() stays lossy for it, like stop_mode).
  - `validate()`: reports `risk_trail_pct < 0`.
  - `warnings()`: warns (not errors) when `risk_trail_pct > 0` with
    `stop_mode != "sigma"` (percent barriers still work; the fraction is of the
    percent TP distance).
- `src/entropy/bot/runner.py`: plumbed into `RiskManager` at construction
  (line ~95) and hot-applied in `apply_config` via `set_risk_trail` (line ~292).
- `src/entropy/bot/portfolio.py`: NOT touched — `PositionState` is a plain
  mutable dataclass; the ratchet mutates `pos.stop_px` directly (no helper
  needed).
- Harness CLI: `--risk-trail-pct` (float, default 0.0) in
  `scripts/entropy_accuracy_btc15m.py` → `RiskOverrides(risk_trail_pct=...)`;
  reported in the report.json `risk` block + levers line.
- `scripts/entropy_wr_sweep.py`: HAS a CLI → added `--risk-trail-pct` (default
  0.0) as a passthrough applied to EVERY combo via
  `build_cfg(c, risk_trail_pct=...)` (new keyword arg; existing callers/tests
  keep working).

### Barrier-capture invariant (T1T2-M3, parked) — RESOLVED for the ratchet

The `_BarrierLedger` capture-at-open invariant was invalidated by the ratchet
(a stop can now fire at a level that differs from the one anchored at open).
The harness now:
- records `_BarrierLedger.exit_levels` (symbol → `(stop_px, tp_px)`) at
  mechanical exit ORDER-EMISSION time — the position is still open then, so
  the stop reflects any ratchet that tick triggered (recorded in the same
  `_check_exits` wrapper);
- the pairing walk prefers `exit_levels` over the open-captured levels for
  `stop`/`take_profit` fills (TP is never ratcheted, so its exit level == open
  level; open capture retained as fallback and for `close` intents untouched).
- With grace=0 and trail=0 the exit levels equal the open levels → the existing
  harness tests (level fills, both-in-bar stop-first) are bit-identical.

## Test list (22 new, all in tests/bot/)

`tests/bot/test_risk_trail.py` (T7, risk layer + config + plumbing, 13 tests):
1. test_ratchet_to_breakeven_at_0_5 — stop == entry after mark crosses 0.5*tp_dist
2. test_ratchet_never_loosens — monotonic (long stop only rises)
3. test_ratchet_keeps_profit_slice_at_1_5 — stop 50% of the way to TP
4. test_ratchet_at_1_0_moves_stop_to_entry
5. test_ratchet_short_side_mirrors — short trigger below entry, stop falls to entry
6. test_ratchet_short_side_profit_slice
7. test_ratchet_off_at_zero_keeps_anchored_stop — existing behavior at 0.0
8. test_ratchet_runs_before_stop_hit_check — the 102.5 trigger tick survives;
   99.5 crosses the NEW stop (above the OLD 95) and stops only because the
   ratchet ran first
9. test_ratchet_does_not_change_tp_hit_behavior — TP still live on trigger ticks
10. test_risk_overrides_validate_risk_trail_pct — negative → ValueError
11. test_validate_reports_negative_risk_trail_pct — validate() defensive gate
12. test_risk_trail_pct_never_handed_to_make_custom — active() lossy, profile() works
13. test_warnings_note_trail_with_percent_mode — warn not error
14. test_runner_plumbs_risk_trail_pct_and_hot_applies — runner + apply_config

`tests/bot/test_entry_grace.py` (T6 + T7 harness, 8 tests):
1. test_same_bar_stop_suppressed_when_grace_ge_1 — grace=0 stops on entry bar, grace=1 rides to TP
2. test_grace_bars_count_from_the_entry_bar — grace=0/1/2 stop at bars 36/37/38
3. test_take_profit_still_live_during_grace — both-in-bar: grace=0 stop-first, grace=1 TP closes in entry bar
4. test_grace_zero_is_byte_identical — r(grace=0) == r(default), report carries the knob
5. test_grace_does_not_reanchor_barriers — post-grace stop clamps to the ANCHORED level
6. test_grace_leaves_strategy_exits_untouched — time stop (max_hold_bars=1) fires inside the grace window with intent 'close'
7. test_grace_exempt_helper_counts_bars_from_entry — unit test of the counting helper
8. test_risk_trail_breakeven_exit_in_harness — r=0.5: stop fires at the RATCHETED breakeven level (~entry, fill NOT re-priced at the deep anchored stop); trail=0 same bars → TP

## Suite result

`pytest tests/bot tests/strategy tests/engine -q` → **460 passed** (baseline
green; the brief's "355" was the count at the T5 close-out — the repo has since
grown to 438 baseline, 438 + 22 = 460). NOTE: `tests/engine/test_engine_perf.py::test_engine_throughput`
(>100k Engine ticks/s) is machine-load-sensitive and fails intermittently under
the current 81% CPU load from parallel opencode/ZCode sessions — proven
environmental by reproducing the identical failure at the base commit b91e644
via a temporary worktree (the test exercises only `entropy.engine`, none of
this round's touched modules). Clean full-suite runs observed: "460 passed".
Smoke-tested the harness CLI end to end offline (synthetic cache): levers line
prints `entry_grace_bars=2, risk_trail_pct=0.5`, report.json `risk` block
carries both, sweep script `--help` shows `--risk-trail-pct`.

## Concerns

- **Grace vs warmup-chained positions:** a position opened during warmup and
  still open at the window start is liquidated at the window's first tick
  (unchanged) — grace never applies to it because it never survives into the
  evaluated feed. Positions opened in the evaluated window get grace counted
  from their true entry tick. No interaction issue observed.
- **Same-bar stops in the ETH report:** the 2 same-bar ETH stops were
  L-tick stops on the entry bar — exactly what grace=1 suppresses
  (test_grace_bars_count_from_the_entry_bar reproduces the pattern on
  synthetic data). Expected to be the main ETH WR lever.
- **`risk_trail_pct >= 1.0` is a dead lever as specified:** the trigger
  `entry ± pct * tp_dist` sits AT (pct=1.0) or BEYOND (pct>1.0) the live TP, so
  the ratchet fires only on ticks that also close the position via the TP —
  the slice stop never governs a real exit (test 3/6 pin the literal formula:
  the stop moves, then the same tick TP-closes). T8 should either sweep only
  {0, 0.5} or the spec needs a trigger cap at the TP (e.g. trigger at
  `min(pct, 1.0) * tp_dist`). Flagged for the T8 selection.
- **Ratchet + `max_hold_bars` interplay:** a time-stopped position ignores the
  ratcheted stop (exits at mark via strategy 'close') — same as today; not
  changed by this round.
- **Percent-mode trail:** allowed with a warning (per brief). The fraction is
  of the percent TP distance.
- **Exit-level capture scope:** `exit_levels` is recorded by the harness
  wrapper for ALL check_exits orders (which are only STOP/TP) — strategy and
  liquidation closes never touch it.
- Not touched per contract: consensus.py, signals.py, engine/*, UI, other
  scripts. `portfolio.py` untouched (direct `pos.stop_px` mutation; the brief
  allowed "only if a helper is needed" — none was).

---

## Review round 1 — I1 fix: FIFO exit-level capture (2026-09-03)

Commit: `fix(bot): FIFO exit-level capture for multi-exit runs (T6T7 review round 1)`
(see `git log -1`). Single implementer, no subagents.

### Finding (verbatim task reviewer, Important I1)

The harness `exit_levels` symbol-keyed dict mis-pairs levels across trades:
the check_exits wrapper wrote `exit_levels[o.symbol] = (stop, tp)` at
order-emission time, but the pairing walk popped it per fill AFTER the whole
feed — a symbol with ≥2 mechanical exits had its LAST write consumed by the
FIRST fill, and later fills popped `None` (falling back to open-anchored
levels, losing any T7 ratchet). The `open_levels` list's own comment warned
exactly against this hazard.

### Fix (scripts/entropy_accuracy_btc15m.py only)

`exit_levels` is now a FIFO list mirroring `open_levels` exactly:

- `_BarrierLedger.__init__`: `dict[str, tuple]` → `list[tuple]` with a comment
  explaining the FIFO alignment (emissions happen in feed order; each emitted
  order produces exactly one fill in that same order — verified against
  `runner._execute` → `executor.submit` → `ledger.record_fill`, so
  append/pop aligns per trade).
- Wrapper: `exit_levels[o.symbol] = ...` → `exit_levels.append(...)` (only for
  orders that actually pass through — grace-suppressed orders emit no fill and
  no entry, keeping alignment).
- Pairing walk: `dummy.exit_levels.pop(fill.symbol, None)` →
  `dummy.exit_levels.pop(0) if dummy.exit_levels else None`. Pops happen only
  on mechanical fills (`stop`/`take_profit`), in the same order emissions
  happened — a one-in-one-out queue per mechanical fill, so every multi-exit
  symbol pairs each fill with ITS OWN emission.

With trail=0 the FIFO entries equal the open-captured levels → byte-identical
(brief item d); with trail>0 every mechanical fill of a multi-exit run now
clamps to that trade's own (possibly ratcheted) stop (brief item g).

### New tests (tests/bot/test_entry_grace.py, 2)

1. `test_two_mechanical_exits_pair_own_levels` (trail=0): two TPs on the same
   symbol, each bar's high OVER-shooting its own TP. Asserts each fill clamps
   to its OWN trade's anchored TP (`entry_px*1.012*(1-SLIP)`), the
   byte-identical requirement. On the old dict trade 1's fill popped trade
   2's higher TP level → the raw overshoot passed through (exit 112.92 vs
   clamped 112.72) and trade 2 popped None.
2. `test_ratcheted_stop_pairing_survives_reentry` (trail=0.5): two stops;
   trade 2's ramp crosses the 0.5*tp_dist trigger before its exit bar, so its
   stop ratchets to breakeven; the exit bar's low sits BELOW breakeven but
   far ABOVE the anchored 1.5% stop. Asserts trade 2's fill is the shallow
   low-tick fill (112.13) — NOT re-priced down to the stale anchored level
   (110.71), which is exactly what the old None-fallback did.

Both tests verified RED against the pre-fix code (git stash of the script:
112.92≠112.72 and 110.71≠112.13 failures) and GREEN with the fix.

### Suite result (round 1)

`pytest tests/bot tests/strategy tests/engine -q` → **461 passed, 1 failed**
(460 baseline + 2 new; the 1 failure is the pre-existing environmental
`test_engine_throughput` flake — 68,998 ticks/s vs the 100,000 threshold
under machine load; exercises only `entropy.engine`, none of this round's
touched modules; identical behavior documented at the base commit).

### Concerns (round 1)

- **M2 (target formula uncapped for pct>2):** left as-is — the one-line
  `min()` cap would land in `risk/manager.py`, outside this round's contract
  (script + harness tests only). Still worth a T8 decision.
- **FIFO alignment invariant:** relies on every emitted STOP/TP order
  producing exactly one fill in feed order (true today: `runner._execute`
  always records the fill; grace suppression skips both emission and fill).
  Any future executor change that can drop a fill would break the alignment —
  the comment on `exit_levels` documents the invariant.
- The warmup ledger's exit_levels are never popped (pairing only reads the
  evaluated ledger) — no cross-window contamination.