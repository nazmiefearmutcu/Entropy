# T9a brief — rolling gate: new levers + per-symbol config (G1)

Part of: docs/superpowers/specs/2026-09-03-winrate-over-60-design.md "Round 2".
The rolling gate (scripts/entropy_wr_gate.py) is the standing verifier of the
user's "WR > 60% with fees" directive. It currently hardcodes the OLD H config
and cannot measure the Round-2 levers. Update it so it measures exactly what
T9 will ship. Independent of the wave4 selection outcome — dispatch in
parallel with the sweep.

BASE: 8972598.

## Why (orchestrator gap-hunt G1, ledger)
`build_ship_default_cfg` hardcodes: vote_mode="adaptive", stop/tp 5/4,
direction 20, hold 96, long_only=True, no risk_trail_pct, and `simulate()` is
called WITHOUT entry_grace_bars (default 0). After T9 ships a per-symbol config
(BTC adaptive, ETH trend per R1; grace and/or risk_trail per OOS), the gate
would measure the WRONG config. The gate must accept and forward all Round-2
levers so it stays 1:1 with what is shipped (the T5 1:1 contract).

## Deliverable — gate parity with the accuracy harness + ship config

`scripts/entropy_wr_gate.py` ONLY (plus its tests). Nothing else.

1. New CLI flags (all optional, defaults = current gate behavior so existing
   scheduled runs are byte-identical):
   - `--vote-mode {adaptive,trend,mean_revert,legacy}` (default "adaptive"):
     passed into the ConsensusConfig of build_ship_default_cfg. (Single mode
     per gate run — the gate runs one symbol at a time like the harness.)
   - `--risk-trail-pct FLOAT` (default 0.0): passed into RiskOverrides.
   - `--entry-grace-bars INT` (default 0): passed into the simulate() call
     (check the simulate signature — it takes entry_grace_bars as a
     top-level kwarg, NOT via cfg).
2. build_ship_default_cfg gains matching keyword params (with the same
   defaults) and forwards them; its docstring keeps the 1:1 contract statement
   and lists the new knobs.
3. Config echo: the gate's report.json "config" block and the console output
   must include the new knob values (vote_mode, risk_trail_pct,
   entry_grace_bars) alongside symbol/bars/warmup_bars/end_ms.
4. Verify the 1:1 contract explicitly: with all new flags at defaults, the
   gate's cfg must be field-identical to the accuracy harness's CLI-default
   cfg (write a test comparing the two cfg objects' relevant fields, or
   comparing build_ship_default_cfg output against the harness's
   build-from-defaults path — read both files and pick the cleanest assertion).

## Files you may touch
- scripts/entropy_wr_gate.py
- tests/bot/test_wr_gate.py (extend; existing 19 tests stay green)
Do NOT touch anything else.

## Tests (required, then full suite)
- New flags parse and forward (unit: build_ship_default_cfg with
  vote_mode="trend", risk_trail_pct=0.5, entry_grace_bars=2 → cfg carries them;
  simulate() receives entry_grace_bars — mock or spy, do not run a live fetch).
- Defaults byte-identical: gate cfg with defaults == harness CLI-default cfg
  on all shared fields.
- report.json config block contains the three new values; console prints them.
- Run `pytest tests/bot tests/strategy tests/engine -q` — must stay green
  (497 baseline; the test_engine_throughput perf flake is environmental).

## Report contract
Write .superpowers/sdd/t9a-report.md: what changed (file:line), 1:1-contract
verification method + result, test list + count, suite result, concerns.
Return: status, commits, one-line test summary, concerns.

## No subagents
Single implementer, no subagents. Commit as
`feat(scripts): rolling gate learns Round-2 levers (parity)`.