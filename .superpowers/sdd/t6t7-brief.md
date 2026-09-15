# T6+T7 brief — entry-bar grace (harness) + risk-trail stop (risk layer + harness)

Part of: docs/superpowers/specs/2026-09-03-winrate-over-60-design.md "Round 2"
(Eth cross-market failure). Tasks T6 and T7 are batched: both are risk-exit
mechanisms plumbed through the same files (accuracy harness, RiskOverrides,
tests). Implement BOTH, commit together, single report.

BASE (must not be touched by you): b91e644.

## Why (root cause)
ETH 15m: 82% of stopped trades never were in profit (MFE≈0), half stop within
1-2 bars, 2 stop on the entry bar itself. Winners are capped early by the 4σ TP
(MFE +7.4% realized +3.5%), losers run to deep 5σ stops (−1.1…−5.8%). Two new
mechanisms attack this; both default OFF until T8 selection proves them.

## T6 — entry-bar grace (harness only, scripts/entropy_accuracy_btc15m.py)

New CLI knob `--entry-grace-bars N` (int, default 0 = off).

Semantics (simulator path only — the live bot is untouched):
- For the first N completed bars after a position opens, the risk layer's
  mechanical stop must NOT be hit-checked against the position:
  * the entry bar itself (the bar whose O tick opened the position) is ALWAYS
    stop-exempt when N >= 1 — this directly kills the 2 same-bar stops;
  * the following N-1 completed bars are also stop-exempt;
  * the TAKE-PROFIT is still live during grace (a winning bar must still close);
  * after grace expires, normal stop/TP hit-checking resumes.
- The stop must NOT be re-anchored by grace: if price crosses the anchored stop
  during grace and then trades above the entry, the position keeps its original
  barriers (grace is a no-op for the barrier values, only for hit timing).
- The strategy's own exits (score/close/time-stop signals) are unaffected by
  grace — it only suppresses the MECHANICAL stop check.
- Where: the harness's per-bar barrier resolution loop (`simulate()` +
  `_BarrierLedger` / whatever resolves stop/TP hits per tick). Find the exact
  hook by reading the code; keep the change minimal and local. The pessimistic
  O,L,H,C tick feed order and level-fill clamp stay as they are.
- Report it in the run's `report.json` config block (`entry_grace_bars`), and
  print it in the levers console line.

## T7 — risk-trail breakeven stop (risk layer + config + harness)

New `RiskOverrides` field `risk_trail_pct: float = 0.0` (0 = off) in
src/entropy/bot/config.py. Plumbing: BotConfig → runner → risk layer; CLI knob
`--risk-trail-pct` in the accuracy harness and the WR sweep script (see T8 —
if the sweep script exists, add the flag there too, default 0.0).

Semantics (live-bot-correct, so it lives in the risk layer, not just the harness):
- Define the position's TP distance at open: `tp_dist = tp_px - entry_px`
  (long) / `entry_px - tp_px` (short).
- `risk_trail_pct` is a FRACTION of that TP distance:
  * `0.0` = off (current behavior).
  * `0 < risk_trail_pct < 1` = breakeven: when mark crosses
    `entry ± risk_trail_pct * tp_dist`, ratchet the stop to entry
    (breakeven — a stopped trade then nets ≈ −fees instead of −5σ).
  * `>= 1.0` = keep a profit slice: ratchet stop to
    `entry ± (risk_trail_pct - 1.0) * tp_dist` (e.g. 1.0 → stop at entry,
    1.5 → stop 50% of the way to TP).
- The ratchet is PER-BAR, monotonic (never loosens), and only moves TOWARD the
  TP (long: stop_px only ever rises, capped at entry+slice; short: only falls).
- The ratchet runs on the same per-tick loop as `check_exits`: before hit
  checking, if the ratchet condition holds, update the position's stop_px.
- Where: RiskManager + Portfolio. Read the existing open/stop/TP flow in
  runner.py/portfolio.py/risk/manager.py and follow it. `stop_tp_prices`
  anchoring at open stays untouched; this is a post-open stop mutation only.
- The barrier-capture invariant (T1T2-M3, parked): the strategy's trail peak and
  the harness `_BarrierLedger` must reflect the ratcheted stop. In the harness,
  the ratchet must be evaluated on the same ticks that resolve stops (the stop
  level a tick sees is the level AFTER any ratchet triggered by that tick).
- Config validation: `risk_trail_pct >= 0`; warn (not error) when
  `risk_trail_pct > 0` with `stop_mode != sigma` (percent barriers still work,
  the fraction is of the percent TP distance).
- Report `risk_trail_pct` in report.json config + console levers line.

## Files you may touch
- scripts/entropy_accuracy_btc15m.py (T6 grace + T7 CLI + report + levers line)
- scripts/entropy_wr_sweep.py (add --risk-trail-pct passthrough ONLY if it has
  a CLI; read it first)
- src/entropy/bot/config.py (RiskOverrides.risk_trail_pct + validation)
- src/entropy/bot/runner.py (plumb risk_trail_pct into RiskManager)
- src/entropy/bot/risk/manager.py (ratchet logic)
- src/entropy/bot/portfolio.py (only if stop_px mutation needs a helper)
- tests/ (NEW tests for grace + ratchet; existing suite stays green)
Do NOT touch: consensus.py, signals.py, engine/*, UI files, other scripts.

## Tests (required, then run the full suite)
- T6: same-bar stop suppressed when grace >= 1; grace bars counted from the bar
  AFTER entry (entry bar = bar where the O tick opened); TP still live during
  grace; grace=0 byte-identical to current behavior; barrier values unchanged
  during grace.
- T7: ratchet to breakeven at 0.5 (stop == entry after mark crosses half TP);
  monotonic (never loosens); 1.5 keeps 50% slice; short side mirrors; ratchet
  off at 0.0 (existing behavior); ratchet-before-hit ordering (a bar that both
  triggers the ratchet and crosses the OLD stop must not stop — it ratchets
  first, then the NEW stop is checked; a bar crossing the NEW stop does stop).
- Run `pytest tests/bot tests/strategy tests/engine -q` — must stay green (355
  baseline + your new tests).

## Report contract
Write .superpowers/sdd/t6t7-report.md: what changed per mechanism, exact
hook points (file:line), test list + count, suite result, any concerns
(especially: does grace interact with warmup-chained positions / the
same-bar-stop trades in the ETH report; does the ratchet need a position
side-aware entry in Portfolio). Return: status, commits, one-line test summary,
concerns.

## No subagents
You are a single implementer. Do not dispatch subagents. Do not commit anything
outside the files above. Commit with a message like
`feat(bot): entry-bar grace + risk-trail stop (Round 2)`.