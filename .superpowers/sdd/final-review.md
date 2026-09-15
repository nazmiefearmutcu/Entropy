# Final Branch Review

Branch: a7a7c1f → b91e644 (11 commits) — win-rate-over-60 accuracy campaign
Repo: C:\Users\Kullanıcı\Entropy
Date: 2026-09-03
Scope: research-only whole-branch integrity review (per-task reviews T1–T5 already passed)

- VERDICT: **PASS** (no Critical issue found)
- Suite verified on disk: `pytest tests/bot tests/strategy` → **355 passed, 1 warning in 28s**
  (matches T5 report; the new harness/sigma/gate/sweep-space test files were re-run
  individually too: 72 passed).

---

## Critical / Important / Minor findings

### Critical
None.

### Important

1. **Design-doc spec body contradicts shipped mechanism (spec item (a)).**
   The design doc's Wave-2 lever (item 3) spec'd `stop_mode: percent|atr` with
   `atr_period`/`stop_atr_mult`/`tp_atr_mult`, and Wave 3 (item 4) spec'd sweeping
   `{stop_mode percent/atr} × {atr multiples} × {risk_trail_pct}`. What shipped is
   **sigma** barriers (`stop_mode: percent|sigma`, multipliers on the entry bar's
   return RMS) and **no `risk_trail_pct` at all**. The T3 brief explicitly authorized
   sigma (docs/superpowers/specs/2026-09-03-winrate-over-60-design.md body lines 48–58
   were never updated even though its own status header, lines 4–7, names sigma 5.0/4.0
   and the ledger calls the task "sigma barriers"). This is a doc-body gap, not a code
   bug — the shipped mechanism is coherent end-to-end (gate/runner/anchoring share
   `barrier_pcts`). Two concrete items for the next cycle:
   - Update the design doc body: ATR → sigma, and either implement or explicitly drop
     the `risk_trail_pct` lever (it appears in the spec's Wave-2 scope but in no brief).

2. **Train/OOS measurement-protocol mismatch (measurement chain).**
   The T4 train sweep calls `simulate()` **cold** (no `warmup_bars`; documented in
   t4-report.md "simulate() is still called without warmup_bars"), while the OOS battery,
   the post-ship verification and `entropy_wr_gate.py` all run warmup-chained
   (`warmup_bars=100`). Selection was train-only (no lookahead — train ended 2026-08-03),
   so this does not leak OOS information, but train rankings are not on the same
   protocol as OOS verification. Recommend the next sweep passes `--warmup-bars 100`
   (or `simulate(..., warmup_bars=...)`) for protocol parity.

3. **Selection evidence for H is weak; OOS is a single-touch sample.**
   H was chosen as the "best-PF family" on a train window where it was **not** profitable
   (train-120d honest: WR 55.4%, PF 0.61; best-PF family PF 0.78) — the guards were
   relaxed after **0/1536** combos were eligible. The 75.0%/PF 1.45 OOS result is the
   only positive signal and it comes from a once-touched window; the number of OOS-tested
   candidates ("top-K") is not documented, so multiple-comparison exposure is
   unquantified. This is honestly disclosed (PROJECT.md regime caveat, wide Wilson CI,
   gate exists for continuous verification) and is a defensible ruling given the WR
   metric target, but it should be flagged: treat H as provisional pending the rolling
   gate's next windows. Recommend documenting K in the ledger.

### Minor

1. **Stale numbers in the design doc Problem section.** "WR 58.1% (31 trades, +0.35%,
   PF 1.15)" (spec lines 13–16) is the pre-harness-fix optimistic measurement; the honest
   T1/T2 re-baseline is +0.10%/PF 0.93 (ledger lines 36–37). PROJECT.md correctly uses the
   honest number. Spec file is gitignored; fix on next touch.
2. **Untracked `.log` at repo root** — leftover redirect artifact of a verification run
   (shows the ship-30d run output). Not committed; should be gitignored/deleted.
3. **ConsensusStrategy ctor defaults diverge from shipped config defaults**
   (`long_only=False`, `max_hold_bars=0` on the strategy vs `True`/`96` in
   `ConsensusConfig`). Intentional and pinned by tests (T5), but a footgun for direct
   strategy construction outside `build_strategies`.
4. **Carried, documented deferred minors** (none blocking): `set_barrier_mode`
   unvalidated values (manager.py:63), T1T2-M1 (costs_paid on extreme-fill notional for
   clamped fills — conservative), T1T2-M2 (stale test docstring), T1T2-M3 (barrier-capture
   invariant), `rank_rows` input mutation, tautological stability test.

---

## Cross-cutting checks table

| Check | Result | Evidence |
|---|---|---|
| (a) Design doc vs shipped contradiction | **PASS** (w/ Important #1) | Body spec'd ATR + risk_trail_pct; shipped sigma (brief-authorized) and no risk-layer trail. Doc body stale; no functional contradiction. |
| (b) Two-task file overlaps conflicting | **PASS** | consensus.py (T1/T3/T5), accuracy script (T2/T3/T5), config.py (T3/T5). T5 reconciled: default-pinning tests updated, `_cfg()` in test_accuracy_harness pins the legacy shape explicitly, `test_default_config_is_shipped_h` pins bare `BotConfig()`. Suite 355 green. |
| (c) Unimplemented in-scope spec item / TODO | **PASS** (w/ Important #1) | `risk_trail_pct` and ATR barriers unimplemented (dropped at brief level; doc-body not updated). No in-scope TODO left; the only `TODO` in `src` is pre-existing and in `risk/barriers.py` (untouched by this branch). |
| (d) WR gate implements the directive | **PASS** | `gate_verdict`: PASS iff `win_rate > 0.60` (strict) AND `trades >= 20` AND `PF >= 1.0` AND `ret >= 0`; Wilson 95% closed form z=1.96 built on exact integer wins (`wins_of`, pnl>0); exit 0/1; cfg built 1:1 with the accuracy script defaults (pinned by test). 19 unit tests green. |
| (e) Docs match reality / honesty | **PASS** | PROJECT.md documents ETH failure (53.6%/PF 0.46), April-May regime failure (55.4%/PF 0.61), 7d tail PF 0.56 on 7t, wide Wilson CI [55.1%, 88.0%], and the 60d-sampled claim; reproduce commands present. progress.md mirrors ledger. No overclaiming. |
| (f) Leftover artifacts / secrets / dead code | **PASS** (w/ Minor #2) | No secrets in src/scripts (grep clean). No TODO/FIXME/debug prints added in shipped files (prints in scripts are intended console/report output). No commented-out code in the diff. Only the untracked `.log` at repo root. |

---

## Measurement chain (end-to-end) verification

- **Intrabar barrier resolution** — direction-aware O,L,H,C feed: long → L before H,
  short → H before L, flat → L,H,C; stop always resolves before TP when both are
  in-range; level-fill clamp takes the worse of (level ± close-side adverse slippage,
  actual fill). Entry can only fire on the O tick (bucket-roll evaluation), so
  just-opened positions are covered by construction. Verified in code
  (scripts/entropy_accuracy_btc15m.py `simulate()._feed`, `_BarrierLedger`) and by
  harness tests (long + short both-in-bar, gap-through, TP clamp, liquidation untouched).
  **No lookahead.**
- **Warmup chaining** — warm slice feeds the same runner (state carries), positions open
  at the window start are liquidated at the first evaluated tick into the warm ledger,
  strategies re-armed via `_notify_closed("warmup_liquidation")`; eval metrics pair only
  eval fills; `warmup_bars=0` is byte-identical to legacy (pinned). Verified in code +
  tests. Note: the sweep still runs cold (Important #2).
- **Long-only reporting** — `win_rate_long_only`/`total_trades_long_only` over LONG round
  trips, printed alongside total; shipped default config is long-only.
- **Fee accounting** — `MarketCostConfig` defaults 10 bps fee / 3 bps slippage per side =
  26 bps round trip (confirmed in config.py:110-111 and the real-run `.log`); executor
  fills embed adverse slippage; `pnl` subtracts both legs' fees; clamped close fees are
  re-priced at the clamped level. `costs_paid` sums the raw extreme-fill notional for
  clamped fills (known deferred minor T1T2-M1, conservative direction, not used by the gate).
- **OOS window isolation** — T4 train sweep = 120d ending **2026-08-03** (pre-ship date);
  OOS = 2026-08-04 → 2026-09-03, untouched during selection; the post-ship verification
  run (no overrides, rolling window ending 09-03 02:14 UTC) reproduces the H battery row
  (WR 75.0%, 24t, PF 1.45, +1.41% vs +1.44% — 0.03pp window-shift). Gate run PASS, exit 0.
  No sampling-bias or lookahead found.

---

## Verdict rationale

The branch is internally consistent and honestly measured: harness fixes land first and
re-baseline every prior number (58.1% optimistic → 58.1%/PF 0.93 honest), the levers are
plumbed end-to-end with a single source of truth for sigma barriers (`barrier_pcts`),
selection was train-only, the OOS window was isolated, the gate implements the standing
directive exactly, and all caveats (ETH, bear regime, wide CI, weak 7d tail) are
documented rather than hidden. The suite is green (355) on disk. The remaining issues
are documentation freshness (design-doc body ATR→sigma, risk_trail_pct scope), a
train-vs-OOS warmup-protocol mismatch, weak train evidence for H (single-touch OOS,
undocumented K), and small hygiene items — none critical.

**Deferred items for the next iteration cycle:**
1. Update the design-doc body (ATR → sigma; implement or drop `risk_trail_pct`).
2. Run future sweeps with warmup chaining for train/OOS protocol parity.
3. Document how many candidates were OOS-verified ("K") before H; treat H as provisional
   until the rolling gate confirms on subsequent windows.
4. Clean up the root `.log` artifact (gitignore or delete).
5. Revisit the carried deferred minors (set_barrier_mode validation, rank_rows mutation,
   T1T2-M1/M2/M3) when those code paths are next touched.