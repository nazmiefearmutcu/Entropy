# Progress Log

Last visited: 2026-08-03T00:00:00Z

- [x] Initialized ORIGINAL_REQUEST.md
- [x] Initialized BRIEFING.md
- [x] Explore the repository to locate configuration files, `AppConfig`, and CLI arguments parsing files.
- [x] Trace configuration loading mechanisms.
- [x] Determine how log paths and trade CSV paths are currently handled and how they can be customized.
- [x] Design fix strategy for Requirement 3 (Configuration Integration).
- [x] Write the handoff report.

## Accuracy & Ship-Readiness pass (2026-08-03)
- [x] Root cause: cost gate amortized over regime window (`consensus.py`, `costs.py`).
- [x] New knobs: `direction_bars`, `confirm_bars`, `trail_pct`, `threshold`, `exit_mode=score|trend_flip|either|hold|trail`, `cost_edge_mult`.
- [x] Defaults: `strategies=("consensus",)`, `exit_mode="trail"`, `trail_pct=0.3`, `direction_bars=0`, `confirm_bars=2`, `min_hold_bars=5`, `cooldown_bars=4`, `threshold=0.5`, `normalize="total"`, SL 1.5 / TP 1.2.
- [x] Accuracy scripts: `scripts/entropy_accuracy_btc15m.py`, `scripts/entropy_grid_search.py`.
- [x] Real BTC 15m results (pre-sweep defaults): 30d −17.09% → −0.96%, 7d −3.82% → −0.03%; 400+ tests green.
- [x] Documented in PROJECT.md (Accuracy & Ship-Readiness section).
- [x] Longer windows + cross-market: BTC 60/90/120d, ETH 30/60d (all vs HODL; defensive pattern confirmed).
- [x] Walk-forward (4 folds x 32 combos, BTC 120d): chained OOS 60d −2.96% best-on-train / −2.63% ship default / HODL −16.43%.
- [x] `--symbol` support in `entropy_accuracy_btc15m.py`; new `scripts/entropy_walk_forward.py`.

## Win-rate sweep: WR > 50% OOS (2026-08-03, subagent-driven)
- [x] Brainstorming skill: design spec docs/superpowers/specs/2026-08-03-winrate-over-50-design.md.
- [x] Installed subagent-driven-development skill from superpowers.
- [x] `scripts/entropy_wr_sweep.py`: WR-focused knob sweep, shardable (Wave 1: 1344 combos, Wave 2: 384).
- [x] `scripts/entropy_wr_verify.py`: OOS verification (30d + 4 walk-forward folds + chained OOS).
- [x] Wave 1 (8 agents): train WR 71.6% max but hollow (PF 0.79).
- [x] Wave 2 (6 agents): train WR 84.2% max but chained OOS negative for all high-WR configs.
- [x] Winner verified OOS: trail/trail_pct=0.3/confirm=2/direction=0/hold=5/cooldown=4/SL 1.5/TP 1.2 → 30d WR 60.7%, chained OOS +0.62%.
- [x] Shipped: ConsensusConfig defaults -> exit_mode=trail, trail_pct=0.3, direction_bars=0, min_hold_bars=5; accuracy script defaults SL 1.5 / TP 1.2.
- [x] UI exit-mode Select now includes trail/hold options.
- [x] Final 30d run: WR 62.1% (29t), PF 0.94, max DD 0.58%, -0.12% return ($100, Binance spot).
- [x] Full test suite green (746 tests).
- [x] Documented in PROJECT.md (Win-rate sweep section).
