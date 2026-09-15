# Final Round-2 Review (whole-branch)

Branch: b91e644 (Round-1 ship H) → 619b452 (10 commits) — ETH cross-market fix
Repo: C:\Users\Kullanıcı\Entropy · Date: 2026-09-03
Scope: research-only whole-branch integrity review (per-task reviews T6T7, T8, T9a, T9 already passed; not re-litigated, only spot-checked for cross-task composition).

- VERDICT: **PASS** (no Critical issue found)
- Suite verified on disk: `pytest tests/bot tests/strategy tests/engine` → **504 collected, 503 passed, 1 failed** = `test_engine_throughput` only, the known environmental perf flake (exercises `entropy.engine`, untouched by this branch; documented in every task report since T6T7).
- Measurement logs verified on disk: `C:/tmp/entropy_t9/{eth_30d,btc_30d,gate_eth,gate_btc}/report.json` reproduce the T9 report's post-ship rows exactly (see (g)).

---

## Critical / Important / Minor findings

### Critical

None.

### Important

1. **60d "OOS" window overlaps train by ~30 days (measurement integrity, (g)).**
   Train = 120d ETH ending 2026-08-03 (≈04-05→08-03). ETH 60d ending 2026-09-03 starts ≈07-05, so ~half the 60d window (07-05→08-03) is IN-sample. The 30d windows (08-04→09-03) are clean, and all gate decisions rest on 30d — but PROJECT.md presents "ETH 60d WR 76.6% PF 0.90" as OOS evidence without disclosing the overlap. The PF-0.90 softness warning is still directionally valid (the OOS half contributes), but the 60d row is not pure OOS. Next cycle: either score a truly out-of-sample 60d (needs data past 09-03) or label the row "mixed train/OOS".
2. **Stop-ladder + f04 refinement tuned ON the headline window (OOS reuse, multiple-comparison).**
   s20's stop 20σ came from a "stop-widening ladder 8→20σ" plus an f04 refinement run against the same ETH 30d window that then headlines at 76.7% and that the gate PASSes on. This is the documented "OOS is the binding gate" methodology (same as Round-1's relaxed guards), and it IS disclosed ("OOS battery picked winner s20 … plus f04 refinement and stop-widening ladder") — but it goes one step further than Round-1: not just selection among K=6 families, but knob-tuning on the verification window. The 30d numbers are therefore optimistic for the stop knob specifically, and the gate PASS on the same window is circular for that knob. Treat s20 as provisional pending the rolling gate on a FRESH window (the gate exists for exactly this). Recommend documenting the ladder's OOS-touch count next cycle.
3. **Walk-forward folds silently dropped (spec item (a)).**
   Design T9 spec'd "OOS = ETH 30d/60d … (+ walk-forward folds)". T9 brief/report/ledger show an 18-run OOS battery + BTC regression, no folds. Probably a conscious scope cut (folds cost sweep-scale compute), but there is no ruling recorded. Carry as an explicit deferred item or run folds next cycle — they are the cheapest defense against Important #2.

### Minor

1. **Design-doc T6 parenthetical not implemented.**
   Round-2 spec item 4 says grace suppresses the stop on the entry bar "(and widened by the entry bar's sigma for the following bars)". Shipped (per T6 brief): pure suppression for entry+N-1 bars, barriers never re-anchored, no sigma widening anywhere. The brief overrode the doc; behavior is tested and coherent — but the doc sentence now describes a mechanism that doesn't exist. Fix the parenthetical on next doc touch.
2. **Design-doc status line stale.**
   Round-2 section header still says "(2026-09-03, in progress)" (spec line 11) although T9 shipped. T9 report admits this ("left as-is … the shipped record lives in PROJECT.md"). One-line fix.
3. **"Bare BotConfig() IS the shipped config" needs its grace asterisk.**
   True for every cfg field (verified: vote trend, hold 192, stop 20/TP 4, trail 0.0), but full shipped behavior ALSO needs `entry_grace_bars=3`, which lives on `simulate()` (signature default stays 0, T6 guarantee intact) and only arrives via the harness CLI / gate defaults. The pinning test's docstring already carries the asterisk ("grace 3 in the harness CLI"), and the gate-harness contract test pins grace=3 forwarding — so this is a wording precision, not a bug. Anyone calling `simulate(klines, BotConfig())` directly gets grace=0 (H-era timing), not s20.
4. **Gate contract test covers cfg subtrees, not top-level fields.**
   `test_gate_cli_defaults_match_harness_cli_defaults` asserts `consensus ==` and `risk_overrides ==` plus grace forwarding — strong, plus live proof (gate ETH report.json ≡ harness ETH report.json: 30t/76.67%/PF 1.3522/+1.7698%). Top-level fields (ema 9/21, momentum 0.15, costs, mode, warmup flag) are hardcoded identically in both mains (verified by reading `main()` in both scripts) but not asserted. Add a top-level equality assert next time either main is touched. Non-shipped knobs (threshold, trail_pct, …) are intentionally gate-flagless — correct by design (gate measures shipped defaults).
5. **Ratchet×grace interplay (unshipped combination).**
   With trail>0 AND grace>0, `_ratchet_stop` still mutates `stop_px` on grace-suppressed bars (the stop moves though the order is suppressed). Shipped config has trail=0.0 so this is dormant; if trail ever ships, pin the intended semantics (ratchet-during-grace vs freeze) with a test.
6. **Test-count drift vs reports (±1, informational).**
   Disk: 504 collected (e.g. `test_vote_mode_per_symbol` 16 = 15 + H1's warmup-divergence test; `TestRound2Levers` 6 = T9a's 5 + T9's extension). Reports say 503 at T9 close. All deltas are explained additions; no missing tests.
7. **Carried, still-deferred items (none blocking):** design-doc ATR→sigma body wording; `set_barrier_mode` unvalidated values (manager.py:63); `rank_rows` input mutation; T1T2-M1 (costs_paid on extreme-fill notional, conservative) / M2 (stale docstring) / M3 (barrier-capture invariant — now per-exit via FIFO `exit_levels`, invariant comment updated in-code); UI Select misrenders dict vote_mode (G2, scope decision: settings-file only); gate `--risk-trail-pct` accepts negatives, rejected downstream (matches harness CLI).

---

## Cross-cutting checks table

| Check | Result | Evidence |
|---|---|---|
| (a) Design-vs-shipped: every Round-2 spec item shipped or ruled | **PASS** (w/ Important #3, Minor #1–2) | T6 grace ✓ (CLI dflt 3 / sim dflt 0); T7 ratchet ✓ (ships 0.0, ruled); sweep {0,0.5} vs spec {0,0.5,1.0} — explicit dead-lever ruling, not silent; per-symbol map ✓ in code, global-trend default — explicit ruling (BTC also improves); walk-forward folds — DROPPED without ruling (Important #3); sigma-widening parenthetical — not implemented (Minor #1) |
| (b) Cross-task composition (defaults → strategies → runner → risk → harness) | **PASS** (w/ Minor #3, #5) | Shipped active path verified: `BotConfig()` = trend/hold192/stop20·TP4/trail0 → `build_strategies` (str → vote_mode_for None, `_mode_for` ≡ trend) → runner plumbs `risk_trail_pct` + hot-apply → `_ratchet_stop` no-op at 0 → harness CLI grace 3 suppresses mechanical stops (live proof: 0 stops / 30 ETH trades). Per-symbol machinery dormant but tested (16 tests). FIFO `exit_levels` keeps T7 pairing exact at trail 0 (byte-identical) |
| (c) T6 grace=0 / T7 trail=0 guarantees pinned | **PASS** | `test_grace_zero_is_byte_identical`, `test_ratchet_off_at_zero…`, `RiskOverrides().risk_trail_pct == 0.0`, `simulate()` sig default 0 (asserted in gate-parity test), gate builder defaults 0/0.0 |
| (d) Gate 1:1 contract | **PASS** (w/ Minor #4) | All 7 shipped knobs covered (vote/stop/tp/hold/db/grace/trail) with flags + echo + report.json; both-mains stubbed contract test green; LIVE proof: gate_eth report.json ≡ eth_30d report.json to 4 decimals, verdict PASS 4/4 |
| (e) Docs honesty | **PASS** | Selection story truthful (0-eligible both modes, K=6 = 5 trend top-WR + 1 adaptive best-PF + f04 + ladder 8→20σ); numbers match disk logs; 60d PF 0.90 soft + 7d weakness (PF 0.23, 8t) + wide-stop standing risk all disclosed; CIs verified ([0.5907,0.8821] in gate report.json); every reproduce-command flag exists (incl. 619b452 legacy-cmd fix) |
| (f) Secrets / dead code / artifacts | **PASS** | No secrets (only env-var names in untouched `smoke_equity.py`); no TODO/prints added in the 17 touched files (sole TODO is pre-existing `barriers.py:170`); no root `.log`; per-symbol map is ruled, tested, documented — not dead code |
| (g) Measurement integrity (no-override, OOS isolation) | **PASS w/ Important #1–2** | Verify runs are bare-default: report.json shows sigma 20/4, grace 3, trail 0.0, warmup 100+0 trades, canonical symbols; 30d windows isolated from train (ended 08-03 → OOS from 08-04). Exceptions: 60d overlaps train ~30d (#1); stop knob tuned on headline window (#2) |

---

## End-to-end shipped-path verification (read, not run — network)

`BotConfig()` defaults (`config.py`: vote `"trend"`, hold `192`, stop `20.0`/TP `4.0`, trail `0.0`) → `build_strategies` resolves str form (fallback unused, map None) → `BotRunner` constructs `RiskManager(..., risk_trail_pct=0.0)` + `set_risk_trail` hot-apply → harness `main()` CLI defaults (`--vote-mode trend`, `--stop-sigma-mult 20`, `--max-hold-bars 192`, `--entry-grace-bars 3`, `--risk-trail-pct 0.0`) build the identical cfg → `simulate(..., warmup_bars=100, entry_grace_bars=3)` → gate `build_ship_default_cfg` defaults identical + forwards grace 3. Chain confirmed identical at every handoff; the two live report.json pairs prove it numerically.

## Deferred items for the next cycle

1. Fresh-window rolling-gate confirmation of s20 (addresses Important #2; the current PASS is on the selection window).
2. Walk-forward folds (Important #3) or an explicit ruling dropping them.
3. Label or replace the mixed 60d row (Important #1).
4. Design-doc touch-ups: Round-2 status → shipped; T6 sigma-widening parenthetical; ATR→sigma body wording (carried from Round-1).
5. Ratchet×grace semantics test if trail ever ships (Minor #5); top-level gate-contract assert (Minor #4).
6. Carried minors: `set_barrier_mode` validation, `rank_rows` mutation, T1T2-M1/M2, UI per-symbol Select (G2).
