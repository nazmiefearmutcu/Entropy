# T5 report — ship H + rolling gate + docs

Base: e5064ae → head: b91e644 (commits 894f976, ee027b1, b91e644). No push.
Gate: `.venv/Scripts/python -m pytest tests/bot tests/strategy` — **355 passed** (was 335; +19 new gate tests, +1 default-pinning test, net -0: no coverage deleted).

## 1. H shipped as defaults (894f976)

- `src/entropy/bot/config.py`: `ConsensusConfig.direction_bars` 0→20,
  `long_only` False→True, `max_hold_bars` 0→96; `RiskOverrides.stop_mode`
  "percent"→"sigma", `stop_sigma_mult` 1.5→5.0, `tp_sigma_mult` 1.2→4.0.
  Comments name the verified "H" config and point at PROJECT.md.
- `scripts/entropy_accuracy_btc15m.py` CLI defaults now match: `--direction-bars 20`,
  `--long-only` (default ON, new `--allow-short` to restore shorts),
  `--max-hold-bars 96`, `--stop-mode sigma`, `--stop-sigma-mult 5.0`,
  `--tp-sigma-mult 4.0`. Docstring documents how to reproduce the pre-H legacy shape
  (`--stop-mode percent --max-hold-bars 0 --direction-bars 0` + no `--long-only`).
- `ConsensusStrategy` constructor defaults deliberately left at the no-op shape
  (explicit per test construction); the shipped behavior is carried by
  `ConsensusConfig`/`build_strategies`, which is what every UI/runtime path uses.

### Test updates (no coverage deleted)
- `tests/bot/test_sigma_barriers.py`: defaults test now pins sigma/(5.0, 4.0);
  anchored-barriers test expects 5.0x/4.0x of sigma=0.002 (1.0%/0.8%); hot-apply test
  applies an EXPLICIT percent config (previously relied on the percent default).
- `tests/bot/test_consensus.py`: wiring test's default assertions flipped to
  long_only=True/96/db20; NEW `test_default_config_is_shipped_h` pins the whole bare
  `BotConfig()` (consensus knobs + risk barriers + `validate() == []`).
- `tests/bot/test_accuracy_harness.py`: `_cfg()` now pins the legacy no-op shape
  explicitly (comment explains why: those tests exercise harness MECHANICS on
  synthetic klines including the SHORT side, so long_only/sigma/hold defaults would
  confound what they check).

## 2. Rolling gate (ee027b1)

- `scripts/entropy_wr_gate.py` (new): reuses `simulate()`/`fetch_klines` from the
  accuracy harness; ship-default cfg built 1:1 with the accuracy script's CLI defaults
  (`build_ship_default_cfg`, pinned by a dedicated test). PASS iff WR > 0.60 AND
  trades >= 20 AND PF >= 1.0 AND return >= 0; prints the four numbers + closed-form
  Wilson 95% CI (z=1.96, no scipy; built on the EXACT integer win count from the
  per-trade list, not the rounded rate). Exit 0/1 for scheduling.
- `tests/bot/test_wr_gate.py` (new, 19 tests): Wilson interval (incl. the documented
  OOS-30d CI and the 60d CI, degenerate/large-n/zero/all-win cases, mirror symmetry),
  gate predicate (boundary cases: WR exactly 0.60 fails, PF exactly 1.0 passes,
  return exactly 0.0 passes, all-four-failures reported), config 1:1 parity, wins_of.

## 3. Docs (b91e644 + local spec edit)

- `PROJECT.md`: new "Win rate > 60% OOS (2026-09-03, shipped)" section — selection
  story (T1/T2 re-baseline → T3 levers → 1536-combo train sweep with 0 eligible →
  OOS battery → H), the full evidence table, honest caveats (ETH fails WR 53.6%/PF
  0.46; April-May bear regime train WR 55.4%; 7d tail PF 0.56 on 7 trades; Wilson
  CI on 24t ≈ [55.1%, 88.0%] with the >60% claim resting on the 60d sample too),
  reproduce commands (wr_gate + accuracy + ETH + legacy-shape).
- `progress.md`: T5 entry.
- `docs/superpowers/specs/2026-09-03-winrate-over-60-design.md`: status APPROVED →
  SHIPPED (file is gitignored like the other specs; edit kept local).
- Ledger T5 status updated to COMPLETE.

## 4. Verification run (no override flags, post-default-bake)

Command: `.venv/Scripts/python scripts/entropy_accuracy_btc15m.py --bars 2880 --out C:/tmp/entropy_accuracy/ship_30d`
Data: real Binance BTCUSDT 15m, 2879 evaluated bars + 100 warmup
(2026-08-04 02:30 → 2026-09-03 02:14 UTC — rolling "now" window).

| Metric | Battery H row | This run |
|---|---|---|
| WR | 75.0% | **75.0%** |
| trades | 24 | **24** |
| PF | 1.45 | **1.45** |
| maxDD | 0.46% | **0.46%** |
| return | +1.44% | **+1.41%** (0.03pp window-shift; battery window ended at 09-03 ~00:00) |

Exits: 18 take_profit / 5 stop / 1 close, avg hold 28.09 bars; sharpe 2.35; halted=False.

Gate end-to-end: `.venv/Scripts/python scripts/entropy_wr_gate.py --bars 2880 --out C:/tmp/entropy_wr_gate/run1`
→ WR 75.0% (Wilson 95% CI [55.1%, 88.0%]), 24t, PF 1.45, +1.41% → **PASS**, exit 0.

## Notes for the ledger

- The ledger's quoted OOS-30d Wilson CI (≈ [55.6%, 88.2%]) was a rough
  approximation; the exact closed-form Wilson for 18/24 at z=1.96 is
  [55.1%, 88.0%] (and 32/43 → [59.8%, 85.1%]). PROJECT.md uses the exact values.
- git identity was unset in this checkout; set repo-local to match the prior
  committer (nazmiefearmutcu) before committing.
- Deferred minors carried, not introduced by T5: T1T2-M1/M2/M3, rank_rows input
  mutation, tautological stability test, manager.py set_barrier_mode validation.
