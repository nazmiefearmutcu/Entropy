# T5 Brief — ship the verified default + rolling-gate tooling + docs

Repo: C:\Users\Kullanıcı\Entropy (Windows). Gate: `.venv/Scripts/python -m pytest tests/bot tests/strategy -q`
green (335 tests). Design: docs/superpowers/specs/2026-09-03-winrate-over-60-design.md.

## Verification evidence (measured with the T1-T4 harness, all net of 26 bps round trip)

Candidate H = long_only=True, direction_bars=20, stop_mode="sigma",
stop_sigma_mult=5.0, tp_sigma_mult=4.0, max_hold_bars=96 (everything else = shipped
defaults: exit_mode trail, trail_pct 0.3, threshold 0.5, confirm_bars 2, min_hold 5,
cooldown 4, move_floor 3e-4, cost_edge_mult 1.0):

| Window | WR | trades | ret | PF | maxDD |
|---|---|---|---|---|---|
| OOS 30d (08-04→09-03) | **75.0%** | 24 | +1.44% | 1.45 | 0.46% |
| OOS first 15d (08-04→08-19) | 80.0% | 5 | +0.09% | 1.48 | 0.13% |
| OOS last 15d (08-19→09-03) | 73.7% | 19 | +1.20% | 1.45 | 0.46% |
| 60d (07-05→09-03) | 74.4% | 43 | +1.82% | 1.47 | 0.46% |
| 7d tail | 71.4% | 7 | −0.11% | 0.56 | 0.32% |
| full 150d (04-06→09-03) | 60.2% | 93 | +0.01% | 0.68 | 2.37% |
| train 120d only (04-06→08-03) | 55.4% | 92 | −0.98% | 0.61 | 2.74% |
| ETHUSDT 30d OOS | 53.6% | 28 | −0.53% | 0.46 | 1.57% |

Old ship default on the same OOS 30d: WR 58.1% (31t), +0.10%, PF 0.93 (honest harness).
ETH and the April-May bear regime are honest failures — document, do not hide.

## Task

1. **Ship H as the defaults** (all four components):
   - `ConsensusConfig`: `direction_bars` 0 → 20, `long_only` False → True,
     `max_hold_bars` 0 → 96.
   - `RiskOverrides`: `stop_mode` "percent" → "sigma", `stop_sigma_mult` → 5.0,
     `tp_sigma_mult` → 4.0.
   - scripts/entropy_accuracy_btc15m.py CLI defaults match (--direction-bars 20,
     --long-only, --stop-mode sigma, --stop-sigma-mult 5.0, --tp-sigma-mult 4.0,
     --max-hold-bars 96).
   - Update the tests that pin the old defaults (they exist and will fail — update
     them to the new defaults; do not delete coverage).
2. **Rolling gate script** `scripts/entropy_wr_gate.py`: re-runs the ship-default
   accuracy gate on the rolling 30d window ending now (or --end-date) with
   configurable --bars (default 2880). PASS iff win_rate > 0.60 AND trades >= 20 AND
   profit_factor >= 1.0 AND total_return_pct >= 0. Print PASS/FAIL + the four numbers
   + Wilson 95% CI for the win rate (z=1.96, no scipy — closed form). Exit code 0 on
   PASS, 1 on FAIL so it can be scheduled. Reuse simulate()/fetch_klines imports.
   Unit-test the Wilson interval + the gate predicate in tests/bot/test_wr_gate.py.
3. **Docs**: append a "Win rate > 60% OOS (2026-09-03)" section to PROJECT.md with
   the evidence table above, the selection story (train sweep 1536 combos → 0
   eligible on the harsh train window → OOS verification battery → H), the honest
   caveats (ETH fails; April-May bear regime WR 55.4%; 7d PF 0.56 on 7 trades;
   Wilson CI on 24 trades ≈ [55.6%, 88.2%] so the >60% claim rests on the 60d
   sample too), and the reproduce commands (wr_gate + accuracy runs). Update
   progress.md. Update docs/superpowers/specs/2026-09-03-winrate-over-60-design.md
   status line to shipped.
4. **Verification run (you do this)**: after baking defaults, run
   `.venv/Scripts/python scripts/entropy_accuracy_btc15m.py --bars 2880 --out
   C:/tmp/entropy_accuracy/ship_30d` with NO override flags and confirm the report
   matches the H row above (WR 75.0%, 24t — identical config + identical data cache
   means identical numbers). Put the actual numbers in your report.

Constraints: only config.py, consensus.py defaults if needed, accuracy script,
entropy_wr_gate.py (new), its test, PROJECT.md/progress.md/spec edits, and the
default-pinning tests. No new deps. Suite green.
Report: .superpowers/sdd/t5-report.md. Final message: ONLY status, commits, one-line
test summary + the verification-run numbers, concerns.
