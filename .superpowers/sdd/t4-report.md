# T4 Report — wave3 sweep space + selection guards

Date: 2026-09-03
Commit: e5064ae `feat(scripts): wave3 sweep space with selection guards`
Files touched: `scripts/entropy_wr_sweep.py`, `tests/bot/test_wr_sweep_space.py` (new). Nothing else.

## What landed

### 1. `--space wave3` (1536 combos)

`WAVE3` space dict in scripts/entropy_wr_sweep.py:

- long_only: [True, False]
- trail_pct: [0.2, 0.3, 0.5] (exit_mode fixed "trail")
- threshold: [0.5, 0.65]
- confirm_bars: [1, 2]
- direction_bars: [0, 20]
- min_hold_bars: [3, 5]
- cooldown_bars: [2, 4]
- barriers (stop_mode, stop_sigma_mult, tp_sigma_mult): ("percent",1.5,1.2), ("sigma",4.0,3.2), ("sigma",5.0,4.0), ("sigma",6.0,4.8)
- max_hold_bars: [0, 96]

Total = 2*3*2*2*2*2*2*4*2 = 1536 (pinned by test). `combos()` explodes the barrier
tuple into stop_mode/stop_sigma_mult/tp_sigma_mult and keeps sl/tp fixed at
1.5/1.2 for every entry (the percent fallback a sigma entry falls back to when a
signal carries no usable sigma). `build_cfg()` wires max_hold_bars/long_only into
ConsensusConfig and stop_mode/stop_sigma_mult/tp_sigma_mult into RiskOverrides via
`.get()` defaults, so full/wave2 combos build exactly as before.

`full`/`wave2` spaces untouched: same sizes (1344 / 384), same key format, same
CSV shape (9 columns, no new fields) — verified by a wave2 smoke run.

### 2. wave3 combo key

Short-form, stable, unique across all 1536 (pinned):

`lo=T/tr0.2/th0.5/cb1/db0/mh3/co2/sl=p1.5x1.2/hold0`
`lo=F/tr0.3/th0.65/cb2/db20/mh5/co4/sl=s4.0x3.2/hold96`

Percent barriers use `p{sl}x{tp}`, sigma barriers use `s{stop}x{tp}`.

### 3. Selection guards (wave3 only)

New flags: `--min-pf` (1.0), `--min-return` (0.0), `--max-dd` (5.0); `--min-trades`
(20) already existed. `is_eligible()` = trades >= min_trades AND pf >= min_pf AND
return_pct >= min_return AND max_dd_pct <= max_dd (boundaries inclusive).

`rank_rows()`: wave3 stamps an `eligible` bool on EVERY row and sorts
eligible-first by win_rate then return_pct (ineligible rows still reported, ranked
among themselves by the same keys). Legacy spaces keep the old behavior verbatim
(min-trades split, pure win_rate order).

CSV: wave3 columns = legacy 9 + `eligible, long_only, stop_mode, max_hold_bars`
(fieldnames are now explicit constants `CSV_FIELDS` / `WAVE3_CSV_FIELDS`, which
also removes the old crash when zero rows ranked). Legacy spaces keep the exact
9-column shape.

### 4. Shard determinism

Unchanged mechanics (`combos[shard::shard_total]` == i % shard_total == shard).
New tests pin: determinism across enumerations, disjointness, completeness, and
balance (max-min <= 1) for a 7-way split of the 1536 combos.

## Tests

`tests/bot/test_wr_sweep_space.py` — 20 tests, importlib pattern from
test_accuracy_harness.py. Pure enumeration/config/selection logic (no simulation,
no network): space sizes, knob expansion, barrier explosion, no wave3 fields on
legacy combos, shard partition properties, key uniqueness/stability/short forms,
build_cfg wiring (wave3 + legacy defaults), build_row column sets, guard
boundaries, eligible-first ranking, legacy ranking unchanged.

Gate: `.venv/Scripts/python -m pytest tests/bot tests/strategy -q` → **335 passed**
(315 baseline + 20 new).

## Smoke runs (real data, 300 bars from baseline_30d_now klines cache)

- wave3: `--bars 300 --space wave3 --shard 0 --shard-total 512` → 3 combos, 1s,
  valid CSV with all 13 columns; eligible=False rows on the tiny window (pf<1.0)
  correctly flagged; top line printed with the short-form key.
- wave2 (backward compat): `--bars 200 --space wave2 --shard 0 --shard-total 384`
  → 1 combo, valid 9-column CSV, legacy key format.

Output: `C:/Users/Kullanıcı/AppData/Local/Temp/entropy_accuracy/wr_smoke/`.

## Notes / concerns for the orchestrator

- The full 1536-combo sweep is intentionally NOT run here (orchestrator's job).
  Rough cost estimate from the smoke run: ~0.3-0.4 s/combo at 300 bars on this
  machine → at 5760 bars expect ~2-8 s/combo, i.e. ~1-3.5 h single-threaded;
  shard across agents as with wave1/wave2.
- sigma-barrier entries (s4.0x3.2 / s5.0x4.0 / s6.0x4.8) widen both barriers a
  lot vs the 1.5/1.2 percent shape; on short windows (300 bars) they produced
  almost no closed trades in the smoke shard — expected, not a bug. Full-window
  runs are where their trade counts become meaningful (the min-trades guard
  exists for exactly this).
- `simulate()` is still called without `warmup_bars` (sweep keeps the legacy
  cold-start semantics of the previous waves); the harness flag exists if a later
  wave wants warmup chaining in the sweep.
- Legacy `rank_rows` rest-split now uses `trades < min_trades` instead of the old
  `r not in eligible` dict-membership check — same semantics, no behavioral change.
