# T8 brief — per-symbol vote_mode + ETH train sweep (grace + risk-trail space)

Part of: docs/superpowers/specs/2026-09-03-winrate-over-60-design.md "Round 2"
(ETH cross-market failure). Selection task: sweep the NEW lever space on TRAIN
data only (120d ETHUSDT 15m ending 2026-08-03), rank, and hand the top-K
families to the orchestrator for OOS verification (T9).

BASE (must not be touched by you): 238ab1f.

## Why (Round-2 root causes, measured)
- R1 regime misclassification: ETH Kaufman eff 0.053 < trend_er 0.35 → adaptive
  never reads momentum; `vote_mode=trend` lifted ETH WR 53.6→60.9% (PF 0.67).
  BUT trend mode regresses BTC (75→61.5%) → the fix must be PER-SYMBOL
  (BTC stays adaptive, ETH may use trend).
- R2 IAE: 82% of stopped trades never in profit; half stop within 1-2 bars;
  2 same-bar stops → `--entry-grace-bars` (T6) attacks this.
- R3 TP caps winners: wide-TP configs reach PF 1.25-1.39 but WR 39-42% →
  `--risk-trail-pct 0.5` (T7 breakeven) attacks the 2.5x loss asymmetry so
  WR>60 ∧ PF≥1 can coexist.
- Ruling (ledger, T6T7): risk_trail_pct is swept as {0, 0.5} only — values
  >= 1.0 are a dead lever (trigger at/beyond live TP).

## Deliverable A — per-symbol vote_mode (bot code)

`src/entropy/bot/config.py`: extend the consensus config so vote_mode can be a
PER-SYMBOL map. Keep backward compat: the current single `vote_mode: str` field
stays valid. Design:

- `ConsensusConfig.vote_mode: str | dict[str, str]` — a plain string keeps
  current behavior; a dict maps raw symbol ("BTCUSDT") -> mode. Validation:
  keys are the configured trading symbols (or a superset — warn, don't error,
  on unknown keys), values in VOTE_MODES.
- `build_strategies` (config.py) resolves per-symbol mode at strategy
  construction: the ConsensusStrategy must select its vote mode per symbol.
  Look at how ConsensusStrategy stores per-symbol state (`_states` dict) and
  thread the mode through `_evaluate` (currently reads `self.vote_mode`).
  MINIMAL change: keep `self.vote_mode` as the default/fallback; add
  `self._vote_mode_for: dict[str, str]` resolved at construction from the
  per-symbol map; `_evaluate` uses `self._vote_mode_for.get(symbol,
  self.vote_mode)`. Also the `tilt_weights`/legacy-pinning logic must honor the
  per-symbol mode the same way it does `self.vote_mode` today.
- `BotConfig` YAML plumbing: `vote_mode:` key accepts either form; add a test
  that a dict form parses and builds.
- The accuracy harness CLI stays as-is (single --vote-mode for the whole run);
  the per-symbol map is a BOT-LEVEL config feature used by the shipped default.

## Deliverable B — sweep space + guards

`scripts/entropy_wr_sweep.py` (read it first — it already has the wave3 space
with long_only/max_hold/stop_mode/sigma mults and selection guards). Extend its
space for the ETH Round-2 wave:

- vote_mode: {adaptive, trend} (the per-run CLI flag the sweep already has —
  check how it passes vote_mode; the sweep itself stays single-mode per run;
  per-symbol mapping is a T9 ship-time concern).
- entry_grace_bars: {0, 1, 2, 3}
- risk_trail_pct: {0.0, 0.5}
- keep existing knobs: stop_sigma_mult {3,4,5,6}, tp_sigma_mult {4,6,8,12},
  max_hold_bars {48,96,192}, direction_bars {0,10,20}, long_only=True (ETH spot
  deployable subset; Round-2 is long-only).
- Warmup parity (deferred item): the sweep must pass `--warmup-bars 100` so
  train protocol == OOS protocol (cold-start train rankings were the T4
  protocol mismatch). Verify the sweep script supports it; add if missing.
- Selection guards: same as T4 — rank by WR with hard guards (trades >= 20,
  PF >= 1.0, ret >= 0); if 0 eligible, relax to top-K-by-WR + best-PF families
  for OOS (T9 is the binding gate). Record K (candidate count) in the output.

Also add a `--symbol` passthrough to the sweep script if it does not have one
(it must sweep ETHUSDT klines, not BTCUSDT).

## Files you may touch
- src/entropy/bot/config.py (ConsensusConfig.vote_mode union + build_strategies
  + YAML parsing + validation)
- src/entropy/bot/strategies/consensus.py (per-symbol mode resolution in
  _evaluate, minimal)
- scripts/entropy_wr_sweep.py (space extension + warmup + symbol passthrough +
  K recording)
- tests/ (NEW: per-symbol vote_mode parsing/building/strategy selection tests;
  sweep-space tests for the new knobs)
Do NOT touch: scripts/entropy_accuracy_btc15m.py (T6T7 territory), engine/*,
UI files, runner.py, risk/manager.py, portfolio.py.

## Tests (required, then full suite)
- Per-symbol vote_mode: dict parses from YAML; string form still works
  (byte-compat); unknown-key warning; strategy selects mode per symbol (two
  symbols, different modes, both exercised); legacy pinning intact for
  per-symbol "legacy" entries.
- Sweep space: new knobs present in the expanded space grid; warmup flag
  passed; K recorded in output; symbol passthrough works.
- Run `pytest tests/bot tests/strategy tests/engine -q` — must stay green
  (461 baseline; the test_engine_throughput flake is environmental, ignore).

## Report contract
Write .superpowers/sdd/t8-report.md: what changed per deliverable (file:line),
space grid size (count combos), guards + relax behavior, K recording, test
list + count, suite result, concerns (especially: does the per-symbol mode
interact with warmup chaining; does build_strategies handle the union type in
every call site). Return: status, commits, one-line test summary, concerns.

## No subagents
You are a single implementer. Do not dispatch subagents. Commit with messages
like `feat(bot): per-symbol consensus vote_mode` and
`feat(scripts): Round-2 ETH sweep space (grace + risk-trail + warmup parity)`.