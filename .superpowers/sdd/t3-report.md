# T3 Report — accuracy levers: long_only, sigma-scaled barriers, time stop

**Status: DONE** (one documented deviation, plus two scope notes, all below)
**Date:** 2026-09-03
**Base HEAD at start:** post-T1T2 (`c313e81` line; 288 tests green)
**Commits (in order):**
- `276ad9e` feat(bot): long_only flag, max_hold_bars time stop, entry-bar sigma on consensus signals
- `3fd4053` feat(bot): sigma-scaled stop/TP barriers via RiskOverrides stop_mode + runner plumbing
- `b0606b2` feat(scripts): --long-only/--max-hold-bars/--stop-mode/--*-sigma-mult flags in accuracy harness

**Test summary:** `.venv/Scripts/python -m pytest tests/bot tests/strategy -q` → exit 0,
**309 collected** (288 pre-existing, all unmodified and passing, + 12 consensus-file
tests + 9 new `tests/bot/test_sigma_barriers.py`), zero failures. The new tests were
verified to fail on the pre-change code via `git stash` round-trip (the new test file
is untracked so it survived the stash and ran against the old source: RiskOverrides /
Signal / stop_tp_prices signature changes all error or fail as expected).

## 1. `long_only` flag (default False)

- `src/entropy/bot/signals.py` — untouched by this item.
- `src/entropy/bot/strategies/consensus.py`: new `long_only: bool = False` ctor kwarg.
  In the entry path, immediately after `sgn` is computed: `if self.long_only and sgn < 0`
  → reset `st.streak`/`st.streak_dir` and return `[]` — the short is never emitted and
  never leaves a short streak half-built. Documented as matching spot-deployable
  reality (Binance spot cannot open shorts live). Default False is bit-for-bit legacy
  (pinned by `test_long_only_default_false_keeps_shorts`).
- `src/entropy/bot/config.py`: `ConsensusConfig.long_only: bool = False`;
  `build_strategies` passes it through.
- Tests: `test_long_only_never_emits_enter_short` (downtrend path that yields exactly
  one ENTER_SHORT normally → zero signals, `direction == 0`, `streak == 0`, 3 seeds),
  `test_long_only_still_enters_long`, the default-off pin, and a wiring test
  (`test_config_wiring_long_only_and_max_hold_bars`).

## 2. Time stop (`max_hold_bars`, default 0 = off)

- `consensus.py`: new `max_hold_bars: int = 0` ctor kwarg. In the in-position block,
  AFTER the trail-anchor accumulation and BEFORE the `min_hold_bars` early-return:
  `if self.max_hold_bars > 0 and st.bars_in_trade >= self.max_hold_bars` → emit the
  existing `SignalAction.EXIT` signal with reason `f"time stop after {held} bars
  (max_hold_bars=N)"`, and reset `direction`/`bars_in_trade`/`bars_since_exit` exactly
  like the normal EXIT path. Placed above the min_hold gate so the time stop is
  genuinely independent of it.
- Validation at construction: `max_hold_bars < 0` → ValueError; `0 < max_hold_bars <
  max(0, min_hold_bars)` → ValueError (a time stop that expires before min_hold lifts
  can never fire — contradictory config). `config.validate()` adds the same rule as a
  human-readable problem (defense-in-depth; see Scope note 2).
- `config.py`: `ConsensusConfig.max_hold_bars: int = 0` + build_strategies wiring.
- Tests: `test_time_stop_fires_at_the_configured_bar` (exit_mode="hold" + cooldown
  1<<30 → the only possible EXIT is the time stop, exactly 3 completed bars in, reason
  contains "time stop", state reset), `test_time_stop_is_off_by_default`,
  `test_time_stop_validation_requires_min_hold_compatibility` (raises for 3<5 and -1;
  accepts 0 and 5==5).

## 3. Sigma-scaled barriers (default percent = unchanged)

- `signals.py`: `Signal` gains trailing field `sigma: float | None = None` (per-bar RMS
  of returns, a fraction). Backward compatible — all existing constructions unchanged.
- `consensus.py`: at the ENTRY branch (after all entry gates pass), computes
  `sigma = self._bar_move_rms(closes)` fresh at the entry bar, sets the cost-gate cache
  `self._last_move_rms[symbol]` to the same value (same number the cost gate computed,
  by construction), and stamps it on the ENTRY signal only. EXIT signals carry
  `sigma=None` (pinned by test).
- `config.py` — `RiskOverrides` gains `stop_mode: str = "percent"`,
  `stop_sigma_mult: float = 1.5`, `tp_sigma_mult: float = 1.2` (defaults mirror the
  shipped 1.5%/1.2% shape at sigma ≈ 0.001). `__post_init__` raises ValueError for
  `stop_mode not in {"percent","sigma"}` or non-positive mults (msgspec 0.21.1 calls
  `__post_init__` on construction AND on json/decode paths — verified). CRITICAL fix
  folded in: these three fields are excluded from `RiskOverrides.active()` via module
  constant `_RISK_BARRIER_FIELDS`, because `active()` feeds `make_custom()` →
  `msgspec.structs.replace(MEDIUM, ...)` and would otherwise raise on every
  `cfg.profile()` call (they are not RiskProfile fields; the runner consumes them
  directly). Verified `BotConfig(risk_overrides=RiskOverrides(stop_mode="sigma")).profile()`
  still resolves.
- `risk/manager.py`: `stop_tp_prices(side, entry_px, symbol, *, stop_pct=None,
  tp_pct=None)`. When BOTH overrides are given and > 0, they replace the profile's
  base percents and the tick-window volatility `scale_factor` is SKIPPED — sigma IS the
  entry bar's volatility; applying the tick-window scale again would double-count
  (documented in the docstring). The `_MAX_STOP_TP_PCT = 50.0` clamp applies in both
  modes. Without overrides, byte-for-byte legacy behavior (the 22/21 alternating-window
  scaling test still passes unmodified).
- `runner.py`: `__init__` reads `stop_mode`/mults from `config.risk_overrides` and
  keeps `self._entry_sigma: dict[str, float]`. `on_trade` stashes `sig.sigma` for entry
  signals before `risk.evaluate`, pops it on rejection (a refused entry must not leave
  a stale sigma for a later unrelated entry — pinned by test). The position-open path
  in `_execute` pops the stash: sigma mode + usable sigma → passes
  `stop_pct = stop_sigma_mult * sigma * 100`, `tp_pct = tp_sigma_mult * sigma * 100`
  into the existing percent barrier computation; percent mode or missing/zero sigma →
  exactly the legacy `stop_tp_prices(pos_side, fill.price, symbol)` call.
  `apply_config` re-reads the three fields so hot-apply keeps them current.
- Tests (`tests/bot/test_sigma_barriers.py`, new, 9 tests): override ignores the
  tick-window scale factor (long + short), 50% clamp still applies, no-override path is
  legacy (22/21 scale), RiskOverrides construction validation + default shape +
  `profile()` works with sigma mode, validate() names the contradictory time stop,
  runner plumbing anchors `stop = fill*(1-1.5σ)`, `tp = fill*(1+1.2σ)` on a real
  `portfolio.open` (equity slippage resolved → fill 100.02), stash consumed and cleared,
  rejection drops the stash, missing sigma and percent mode both fall back to the
  MEDIUM 1%/2% barriers.

## 4. Accuracy script flag plumbing (`scripts/entropy_accuracy_btc15m.py`)

- New CLI flags: `--long-only` (store_true), `--max-hold-bars` (int, 0),
  `--stop-mode {percent,sigma}` (default percent), `--stop-sigma-mult` (1.5),
  `--tp-sigma-mult` (1.2). Wired into the `ConsensusConfig` / `RiskOverrides` the
  script already builds; echoed in `report["config"]` (`risk.stop_mode`,
  `risk.stop_sigma_mult`, `risk.tp_sigma_mult`, top-level `long_only`,
  `max_hold_bars`) and in the human-readable block (`levers:` line).
- Defaults preserve current behavior exactly. `--help` verified; module imports clean;
  `py_compile` clean for all five scripts that construct `RiskOverrides`
  (accuracy/wr_sweep/walk_forward/grid_search/wr_verify — the latter four build
  RiskOverrides with keyword args, so new defaulted fields are compatible without
  edits).
- Harness beyond flags: UNTOUCHED. `_BarrierLedger` capture-at-open invariant verified
  by reading the code: `stop_tp_prices` is called in exactly two places —
  `RiskManager.evaluate`'s cost gate (read-only, no state) and `BotRunner._execute`'s
  OPEN path (the only writer). `portfolio.open` stores the barriers;
  `check_exits`/`trip_circuit_breaker` only read them; nothing re-anchors while a
  position is open. Sigma mode changes WHICH levels are anchored at open, not the
  immutability, so the T2 `_BarrierLedger` capture-at-open and the direction-aware
  O/L/H/C feed remain valid unchanged.

## Deviations from the brief

1. **`RiskOverrides.active()` exclusion (not in the brief, required for correctness).**
   The brief said to add the three fields to RiskOverrides; it did not mention that
   `active()` feeds `make_custom()`. Leaving them in would have raised
   `TypeError`/`ValueError` on EVERY `cfg.profile()` call (runner ctor, validate,
   warnings). They are excluded from `active()` and validated separately via
   `__post_init__` ValueError.

## Scope notes (deliberate, documented)

1. **Risk layer's cost gate still uses percent barriers in sigma mode.** The brief
   scoped sigma mode to "BotRunner's position-open path (where stop_tp_prices is
   called)"; `RiskManager.evaluate`'s cost-to-stop / TP-vs-round-trip checks keep using
   the profile's percent barriers, so entry gating in sigma mode judges the configured
   percent structure while the anchored barriers are sigma-scaled. Kept minimal per the
   brief's touch-list and noted here so T4 sweeps interpret cost-gate rejects correctly.
2. **`validate()` duplicates the RiskOverrides ValueError checks** as human-readable
   problems. They are currently unreachable through any construction path (msgspec
   calls `__post_init__` on direct construction and decode — both verified), so they
   are pure defense-in-depth if `__post_init__` semantics ever change.

## Files touched (exactly the brief's allow-list)

- `src/entropy/bot/signals.py` — `Signal.sigma`
- `src/entropy/bot/strategies/consensus.py` — long_only, max_hold_bars, entry sigma
- `src/entropy/bot/config.py` — ConsensusConfig/RiskOverrides fields, `__post_init__`,
  `active()` exclusion, validate(), build_strategies wiring
- `src/entropy/bot/risk/manager.py` — `stop_tp_prices` optional pct overrides
- `src/entropy/bot/runner.py` — sigma stash + open-path barrier override + hot-apply
- `scripts/entropy_accuracy_btc15m.py` — CLI flags + report echo only
- `tests/bot/test_consensus.py` (+12), `tests/bot/test_sigma_barriers.py` (new, 9)

UI files untouched; UI tests untouched; no new dependencies; nothing pushed.

---

# Round 1 — review fixes (sigma-mode entry cost gate)

**Status: DONE**
**Commits:**
- `e07670d` fix(bot): sigma-mode entry cost gate judges the actual sigma barriers
- `f2a90be` docs(bot): warn that RiskOverrides.active() is lossy for barrier fields

**Test summary:** `pytest tests/bot tests/strategy -q` exit 0 — **315 collected**
(309 + 6 new), zero failures.

## IMPORTANT fixed — cost gate judged percent barriers in sigma mode

`RiskManager.evaluate`'s cost gate (`tp_bps <= round_trip_bps`,
`cost_to_stop`) used `stop_tp_prices` with the profile's PERCENT barriers, so in
sigma mode an entry whose actual sigma barriers are cost-dead (BTC-15m-like
sigma ~0.0011 → TP ~13 bps vs the 26 bps Binance spot round trip) was approved,
then anchored to a TP that could never pay its costs.

Fix — one shared source of truth:
- `RiskManager` gains `stop_mode`/`stop_sigma_mult`/`tp_sigma_mult` (constructor
  kwargs, mirroring RiskOverrides), a `set_barrier_mode()` hot-apply setter, and
  a public `barrier_pcts(sigma) -> (stop_pct, tp_pct) | None` helper (sigma mode
  + usable sigma → mult·sigma·100 distances; percent mode or sigma None/0 →
  None = percent fallback).
- `evaluate`'s cost gate now derives its `stop_px`/`tp_px` from
  `barrier_pcts(signal.sigma)` — the SAME choice the runner's open path makes
  when anchoring — so an entry is gated on the barriers it will actually get.
- `BotRunner` passes the mode into `RiskManager` at construction, uses
  `risk.barrier_pcts(sigma)` in `_execute` (runner-local `_stop_mode` fields
  removed — the risk layer is the single holder), and hot-applies via
  `risk.set_barrier_mode(...)` in `apply_config`.

Barriers remain anchored ONCE at open (harness `_BarrierLedger` capture-at-open
invariant untouched — the gate fix changes which entries are approved, not the
immutability of the anchored levels).

New tests (tests/bot/test_sigma_barriers.py, +6): `barrier_pcts` unit behavior;
**sigma-mode entry with sigma=0.0011 is rejected by the cost gate**
(`"take-profit below round-trip cost"` — fails on round-0 code, verified by the
fact the round-0 anchoring test had to move to sigma=0.002 to fill);
sigma=0.005 still approves (60 bps TP > 26 bps RT, cost-to-stop 0.35 ≤ 0.5);
percent mode ignores sigma; missing sigma gates on the percent fallback;
runner construction plumbs the mode into the risk layer and `apply_config`
hot-swaps it.

Side effect documented: the round-0 runner anchoring test now uses sigma=0.002
(a cost-viable sigma) instead of 0.001, because the fixed gate correctly rejects
0.001 with the default config's 8 bps equity round trip.

## MINORs fixed

1. **runner.py `_execute` live-blocked early-return** now pops the
   `_entry_sigma` stash for OPEN intents, so an entry blocked by
   `LiveTradingDisabledError`/`NotImplementedError` cannot leave a stale sigma
   behind for a later unrelated entry on the same symbol.
2. **config.py `_RISK_BARRIER_FIELDS` comment + `RiskOverrides` docstring**
   now explicitly warn that `active()` is LOSSY for the barrier fields — callers
   needing the mode must read `risk_overrides.stop_mode` directly.

## Files touched in round 1

Exactly `src/entropy/bot/risk/manager.py`, `src/entropy/bot/runner.py`,
`src/entropy/bot/config.py` (docstring only), `tests/bot/test_sigma_barriers.py`.
No push.
