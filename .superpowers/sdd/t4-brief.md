# T4 Brief — extended sweep space + selection guards

Repo: C:\Users\Kullanıcı\Entropy (Windows). Gate: `.venv/Scripts/python -m pytest tests/bot tests/strategy -q`
green (315 tests). Design: docs/superpowers/specs/2026-09-03-winrate-over-60-design.md.

Context: T1-T3 landed. The accuracy harness (scripts/entropy_accuracy_btc15m.py) now
resolves intrabar barriers pessimistically (stop-first per direction), clamps mechanical
fills to barrier levels, counts pnl==0 as losses, supports --warmup-bars,
--long-only, --max-hold-bars, --stop-mode percent|sigma, --stop-sigma-mult,
--tp-sigma-mult. scripts/entropy_wr_sweep.py imports build_ticks/simulate from it and
sweeps a knob space sharded via --shard/--shard-total, ranking by win_rate with a
--min-trades filter.

## Task: extend scripts/entropy_wr_sweep.py (and ONLY that script + its tests)

1. **New space `--space wave3`** (keep existing `full`/`wave2` untouched and working):
   - long_only: [True, False]
   - trail_pct: [0.2, 0.3, 0.5] (exit_mode fixed "trail")
   - threshold: [0.5, 0.65]
   - confirm_bars: [1, 2]
   - direction_bars: [0, 20]
   - min_hold_bars: [3, 5]
   - cooldown_bars: [2, 4]
   - barriers: [("percent", 1.5, 1.2), ("sigma", 4.0, 3.2), ("sigma", 5.0, 4.0), ("sigma", 6.0, 4.8)]
     → stop_mode / stop_sigma_mult / tp_sigma_mult (percent entry keeps sl/tp at 1.5/1.2)
   - max_hold_bars: [0, 96]
   Total = 2*3*2*2*2*2*2*4*2 = 1536 combos. Wire every knob into the BotConfig the
   sweep already builds (long_only/max_hold_bars → ConsensusConfig; stop_mode/mults →
   RiskOverrides). The `key` string must include all swept knobs (short forms, e.g.
   `lo=T/sl=s4.0x3.2/hold96`), keep it stable and parseable.
2. **Selection guards** (new, for wave3): a combo is ELIGIBLE only if
   trades >= --min-trades (default 20) AND profit_factor >= --min-pf (default 1.0)
   AND return_pct >= --min-return (default 0.0) AND max_dd_pct <= --max-dd (default 5.0).
   Report ALL rows (eligible flag as a column) but rank eligible-first by win_rate, then
   by return_pct. CSV columns: add `eligible`, keep the existing columns, add
   `long_only`, `stop_mode`, `max_hold_bars`.
3. **Shard determinism**: with fixed --shard/--shard-total the combo partition must be
   deterministic and disjoint (enumerate combos, take i % shard_total == shard).
   Unit-test the enumeration (count, disjointness, determinism) in a new
   tests/bot/test_wr_sweep_space.py importing the script via importlib (follow the
   pattern tests/bot/test_accuracy_harness.py uses for importing scripts/).
4. **Smoke run, not full run**: run ONE shard slice locally with a tiny --bars
   (e.g. --bars 300) to prove the script executes end-to-end and writes valid CSV.
   The FULL sweep run is the orchestrator's job, not yours — do not run it.

Constraints: no changes outside entropy_wr_sweep.py + the new test file; no new deps;
existing spaces' CSV output shape unchanged (backward compatible for old consumers);
append full report to .superpowers/sdd/t4-report.md.
Final message: ONLY status, commit list, one-line test summary, concerns.
