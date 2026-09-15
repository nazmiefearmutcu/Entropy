# T9 brief — ship Round-2 config (global) + gate parity + docs

Part of: docs/superpowers/specs/2026-09-03-winrate-over-60-design.md "Round 2".
Wave-5 selection is DONE by the orchestrator (below). Your job: ship it as the
new default, keep the gate 1:1, document honestly.

BASE: 764c305.

## Selection (orchestrator-measured, honest harness, 26 bps RT)
- Train: 120d ETH ending 2026-08-03, wave4 1152 combos x2 modes (adaptive +
  trend), warmup 100. Both 0 eligible. Trend dominates adaptive on train
  (top-WR 56.8% vs 53.4%).
- OOS battery (30d/60d ending 2026-09-03 + BTC 30d regression), K=6 families:
  winner **s20** = vote_mode trend + stop 16->20σ / TP 4σ + hold 192 + db 20 +
  grace 3 + long_only (all else = H defaults: exit trail 0.3, threshold 0.5,
  confirm 2, min_hold 5, cooldown 4, move_floor 3e-4, cost_edge_mult 1.0).
- Winner evidence:
  - ETH 30d: WR 76.7% (30t) +1.79% PF 1.35 (gate PASS: 4/4)
  - BTC 30d: WR 80.6% (36t) +1.53% PF 1.57 (beats H 75.0%/1.45; no regression)
  - ETH 60d: WR 76.6% (64t) +1.62% PF 0.90 (WR holds, PF soft — document)
  - ETH 7d tail: WR 75.0% (8t) PF 0.23 (tiny sample, 1 big stop — document)
- Ruling (orchestrator): ship s20 GLOBALLY (both symbols — BTC also improves,
  so the per-symbol map is not needed for the default; T8's map stays in code
  for future use). risk_trail_pct ships at 0.0 (train-negative 576/576, BTC-
  wrecking); grace ships at 3 (BTC-harmless, ETH same-bar-stop killer).
- Honesty note for docs: stop 20σ is very wide — the stop almost never fires
  (30d: 2 stops in 30 trades); exits are TP/time-stop/score. A single sharp
  selloff can erase weeks of small TPs (60d PF 0.90 proves it). Document this
  as the standing risk; the rolling gate is the monitor.

## Deliverable 1 — ship the default
- `src/entropy/bot/config.py`: ConsensusConfig defaults -> vote_mode "trend",
  direction_bars 20 (unchanged), max_hold_bars 192 (was 96). RiskOverrides
  defaults -> stop_sigma_mult 20.0 (was 5.0), tp_sigma_mult 4.0 (unchanged).
  (long_only True unchanged; grace is harness-only, no bot default.)
- `scripts/entropy_accuracy_btc15m.py` CLI defaults -> same values
  (--vote-mode trend, --stop-sigma-mult 20, --max-hold-bars 192;
  --entry-grace-bars default 3? NO — keep CLI default 0 (off) and let the
  shipped BotConfig carry grace... wait: grace is a SIMULATE kwarg, not cfg.
  Decision: change the harness CLI --entry-grace-bars default to 3 so bare
  CLI runs measure the shipped behavior; the T6 byte-identical guarantee
  (grace=0) is pinned by tests, not by the default.)
- Update default-pinning tests (test_default_config_is_shipped_* and friends)
  to the new values; harness-mechanics tests keep pinning the legacy shape
  explicitly where they need synthetic shorts (T5 pattern).

## Deliverable 2 — gate parity (follow T9a pattern)
- `scripts/entropy_wr_gate.py`: build_ship_default_cfg must equal the NEW
  shipped default (vote trend, stop 20σ, hold 192, grace 3). Add the missing
  knobs as CLI flags if absent: --stop-sigma-mult, --tp-sigma-mult,
  --max-hold-bars, --direction-bars (T9a added vote/risk-trail/grace; check
  what is still hardcoded and add it). 1:1 contract test must still pass
  (extend it to the new fields).
- Run the gate once per symbol as verification (BTC + ETH, needs network):
  both must PASS with the shipped defaults. If the gate fails on framing
  (e.g. window shift), report it — do not tune the config to the gate.

## Deliverable 3 — docs
- PROJECT.md: append "Round 2 — ETH fixed (2026-09-03, shipped)" with the
  selection story (train 0-eligible both modes, K=6, OOS binding gate), the
  evidence table (30d/60d/7d per symbol + BTC regression + Wilson CIs), the
  standing risk note (wide stop), reproduce commands, and the deferred items
  (design-doc ATR body, sweep warmup legacy note, K documented here:
  K=6 families (5 trend top-WR + 1 adaptive best-PF) + f04 refinement +
  stop-widening ladder 8→20σ).
- progress.md: mirror the ledger's Round-2 close-out in a few lines.

## Files you may touch
- src/entropy/bot/config.py (defaults + validation if needed)
- scripts/entropy_accuracy_btc15m.py (CLI defaults only — NO behavior change)
- scripts/entropy_wr_gate.py (ship-default parity + missing flags)
- tests/bot/test_*.py (default-pinning updates + gate parity extension)
- PROJECT.md, progress.md
Do NOT touch: consensus.py, runner.py, risk/manager.py, sweep script, engine/*,
UI files.

## Tests + verification
- Update + run full suite `pytest tests/bot tests/strategy tests/engine -q`
  (must stay green; test_engine_throughput env flake ignored).
- Verification runs (network): post-ship accuracy run with NO overrides on
  ETH 30d + BTC 30d (must reproduce ~76.7%/1.35 and ~80.6%/1.57 within window-
  shift tolerance) + gate PASS on both symbols. Record exact numbers in report.

## Report contract
Write .superpowers/sdd/t9-report.md: shipped values (file:line), gate parity
method, verification numbers per symbol/window, docs locations, test count,
concerns. Return: status, commits, one-line test summary, concerns.

## No subagents
Single implementer, no subagents. Commit as `feat(bot): ship Round-2 defaults
(trend + wide stop)` + `feat(scripts): gate parity for Round-2 ship` +
`docs: Round-2 ETH evidence`.