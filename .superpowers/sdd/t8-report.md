# T8 report — per-symbol vote_mode + ETH train sweep (grace + risk-trail space)

Date: 2026-09-03. Base: 238ab1f. Status: DONE. Single implementer, no subagents.
Commits:
- `6762a7c` feat(bot): per-symbol consensus vote_mode
- `72e0a13` feat(scripts): Round-2 ETH sweep space (grace + risk-trail + warmup parity)

## Deliverable A — per-symbol vote_mode (bot code)

Root cause R1: ETH's Kaufman efficiency (0.053) < trend_er (0.35) so `adaptive`
never reads momentum on ETH, while `trend` regresses BTC (75→61.5%) — the fix
must be per-symbol. The config field is now a union; a plain string is
byte-compatible.

### src/entropy/bot/config.py
- `ConsensusConfig.vote_mode: str | dict[str, str] = "adaptive"` (line 45):
  dict maps RAW symbol ("BTCUSDT") -> mode; docstring documents the Round-2
  rationale. msgspec decodes both forms from the settings serialization path
  (the repo's config "YAML" — JSON via msgspec; no PyYAML in the repo).
- `validate()` (lines ~369-377): string must be in VOTE_MODES; dict values
  must be in VOTE_MODES (each flagged as a hard problem). Unknown KEYS are not
  an error here.
- `warnings()` (lines ~503-511): when the dict form is used and
  `cfg.symbols` is non-empty, keys not among the configured symbols (matched
  on the raw suffix) produce a warning, not an error. Empty `symbols` (trade
  everything) never warns — the map is a partial override there.
- `build_strategies()` (lines ~407-431): resolves the union at construction —
  `vote_mode = c.vote_mode if isinstance(c.vote_mode, str) else "adaptive"`
  (the strategy-level fallback when the dict form is used) and threads
  `vote_mode_for=dict(c.vote_mode)` into the strategy. Every call site of the
  union is this one function; no other consumer of `cfg.consensus.vote_mode`
  does string arithmetic (runner reports it as JSON, CLI replaces it with a
  str, UI always writes a str).

### src/entropy/bot/strategies/consensus.py
- `__init__(..., vote_mode_for: dict[str, str] | None = None, ...)` (keyword
  only): values validated against VOTE_MODES (ValueError, same as the string
  path). `self._vote_mode_for` (line 343) = shallow copy of the map.
- Legacy pinning (line 349): `vote_mode == "legacy" or "legacy" in
  self._vote_mode_for.values()` pins `normalize="total"` and
  `min_participation=0.0` — the scoring knobs are strategy-global, so a
  per-symbol "legacy" entry pins them for the whole strategy, which is the
  only way that symbol is scored faithfully (identical semantics to the
  single-string legacy today).
- `_mode_for(symbol)` (lines 484-491): `self._vote_mode_for.get(symbol)` with
  a raw-suffix fallback (`symbol.split(":", 1)[1]`) so both RAW keys
  ("BTCUSDT") and canonical keys ("binance-spot:BTCUSDT") resolve against a
  canonical feed symbol; fallback = `self.vote_mode` (the brief's minimal
  `get(symbol, self.vote_mode)` shape plus the suffix probe).
- `_evaluate` (lines ~513-518 and ~533-535): both `self.vote_mode` uses are
  now `mode = self._mode_for(symbol)` — the `momentum_read` decision and the
  legacy flat-weights decision are per-symbol.

## Deliverable B — sweep space + guards (scripts/entropy_wr_sweep.py)

### Space (WAVE4, lines 104-124)
- `WAVE4_BARRIERS` = all 16 (`sigma`, stop σ {3,4,5,6}, TP σ {4,6,8,12})
  barrier shapes — sigma-only, no percent entries.
- `WAVE4` = exit_mode=trail × long_only=True × trail_pct=0.3 × threshold=0.5 ×
  confirm_bars=2 × direction_bars {0,10,20} × min_hold_bars=5 ×
  cooldown_bars=4 × barriers(16) × max_hold_bars {48,96,192} ×
  entry_grace_bars {0,1,2,3} × risk_trail_pct {0.0,0.5}.
  Non-swept knobs pinned at the shipped "H" defaults. risk_trail_pct swept
  {0, 0.5} only per the T6T7 ruling (>= 1.0 is a dead lever).
- **Grid size: 1152 combos** (3 × 16 × 3 × 4 × 2 = 1152).
- `vote_mode {adaptive, trend}` is a **per-run flag** (`--vote-mode`, default
  adaptive): `combos(space, *, vote_mode=...)` overrides FIXED (line 127);
  the sweep stays single-mode per run (per-symbol mapping is a T9 ship
  concern). `combos()` keeps its default signature behavior for the legacy
  spaces (wave3 tests byte-identical).
- `combo_key` appends `vm{...}/eg{...}/rt{...}` ONLY when the combo carries
  the wave4 keys — wave3 keys are unchanged (pinned by test).
- `build_cfg(c, *, risk_trail_pct=0.0, symbol=SYMBOL)` (line 178): the combo's
  `risk_trail_pct` wins over the legacy `--risk-trail-pct` passthrough
  (`c.get("risk_trail_pct", risk_trail_pct)`); `symbols`/`ema_symbol` follow
  the new `--symbol`.

### Warmup parity + symbol passthrough (main, lines ~345-383)
- `--warmup-bars` (default **100**, matching the accuracy harness protocol —
  train == OOS, the T4 protocol mismatch fix) passed to `simulate()`.
- `--symbol` (default RAW "BTCUSDT"; canonical "venue:SYMBOL" accepted as-is,
  raw resolved via `resolve_symbol` → "binance-spot:ETHUSDT") drives
  `build_cfg` AND `simulate(symbol=...)`.
- `--top-k` (default 5) drives the relax width. `entry_grace_bars` is
  combo-driven: `simulate(..., entry_grace_bars=c.get("entry_grace_bars", 0))`.

### Selection guards + K recording (rank_wave4, lines 269-296)
- Hard guards (T4): trades >= 20, PF >= 1.0, return >= 0, max DD <= 5%
  (the brief lists the first three; max DD is kept — T4's guard set and the
  script's existing `--max-dd`).
- When ≥1 row passes: candidates = eligible rows. When **0 eligible**:
  relax to top-K-by-WR ∪ top-K-by-PF families (K = `--top-k`); OOS (T9) is the
  binding gate.
- **K recording**: `K = len(candidates)` is stamped on EVERY row (a
  run-level constant) and written as the `K` column of the wave4 CSV
  (`WAVE4_CSV_FIELDS`, line 237); main prints `K=<n> candidates ...`
  (plus eligible count and whether the run relaxed). Rows rank candidates
  first (WR, then return), then the rest by WR.

## Tests (35 new, all in tests/bot/)

`tests/bot/test_vote_mode_per_symbol.py` (15):
1. test_string_form_stays_byte_compatible
2. test_dict_form_decodes_from_msgspec_and_builds
3. test_string_form_decodes_from_msgspec_as_before
4. test_dict_form_survives_settings_round_trip
5. test_unknown_map_key_warns_but_does_not_fail
6. test_known_keys_do_not_warn
7. test_bad_value_is_a_validate_problem_and_a_constructor_error
8. test_mode_resolves_per_symbol_raw_and_canonical_keys
9. test_two_symbols_different_modes_both_exercised (legacy trend-blind vs
   adaptive trades on the SAME path — per-symbol selection proven end-to-end)
10. test_fallback_mode_applies_to_unlisted_symbols
11. test_legacy_pinning_applies_for_per_symbol_legacy_entries
12. test_legacy_pinning_not_applied_without_a_legacy_entry
13. test_single_string_legacy_still_pins
14. test_build_strategies_passes_map_with_adaptive_fallback
15. test_build_strategies_string_form_has_empty_map

`tests/bot/test_wr_sweep_round2.py` (20):
1. test_wave4_space_has_1152_combos
2. test_wave4_new_knobs_present
3. test_wave4_pins_the_round2_deployable_subset
4. test_wave4_barrier_shapes_are_sigma_3_6_x_4_12
5. test_vote_mode_is_a_per_run_flag_not_a_dimension
6. test_wave4_keys_unique_and_encode_the_new_levers
7. test_wave3_keys_unchanged_by_the_wave4_additions
8. test_legacy_spaces_carry_no_wave4_fields
9. test_build_cfg_wave4_combo_risk_trail_wins_over_passthrough
10. test_build_cfg_legacy_spaces_use_the_passthrough
11. test_build_cfg_symbol_passthrough
12. test_wave4_row_carries_the_new_columns
13. test_wave3_row_has_no_wave4_columns
14. test_wave4_hard_guards_rank_eligible_first_and_record_k
15. test_wave4_relaxes_to_top_k_by_wr_union_top_k_by_pf_when_none_eligible
16. test_wave4_relax_with_top_k_larger_than_rows_keeps_all
17. test_wave4_non_candidates_rank_after_candidates_by_wr
18. test_main_wave4_passes_vote_mode_warmup_symbol_and_writes_k (patched
    simulate: --space wave4 --limit 3 --vote-mode trend --warmup-bars 100
    --symbol ETHUSDT → simulate kwargs + CSV K column + console "K=3")
19. test_main_wave4_relax_uses_top_k_flag
20. test_main_wave3_keeps_legacy_csv_shape

## Suite result

`pytest tests/bot tests/strategy tests/engine -q` → **496 passed, 1 failed**
(461 baseline + 35 new = 496; the 1 failure is the documented environmental
`tests/engine/test_engine_perf.py::test_engine_throughput` flake —
"engine too slow: 54282 ticks/s" vs the 100,000 threshold under machine load;
exercises only `entropy.engine`, none of this round's touched modules. Its
intermittency is load-bound: the identical test passed at base 238ab1f in a
stash-window when load dipped, and fails under load with my changes — same
behavior T6T7 documented. New tests: 35/35 green.)

## Concerns

- **Per-symbol mode vs warmup chaining:** no interaction observed. Warmup
  seeds only the per-symbol close deques/bucket state; the mode resolution in
  `_evaluate` is stateless per bar. A per-symbol "legacy" entry pins normalize
  GLOBALLY (scoring knobs are strategy-global) — this is the faithful
  semantics (legacy scoring is a property of the whole scoring pass), but
  note it means "ETH legacy + BTC adaptive" scores BTC with total normalize
  and zero participation floor too. There is no per-symbol normalize knob;
  the sweep avoids legacy entirely (wave4 modes: adaptive/trend), so this is
  a T9 ship-default concern only.
- **Union handling in every call site:** verified — `build_strategies`
  (config.py:407-431) is the only constructor; `runner.py` records
  `cfg.consensus.vote_mode` verbatim in the report dict (JSON-serializable
  for both forms), `__main__.py` replaces it with a str (union accepts it),
  UI always writes a str. The UI Select would misrender a dict if one were
  ever saved through it — out of contract (UI untouched), flagged for T9 if
  the per-symbol default ships through settings.
- **`_mode_for` suffix probe:** canonical-keyed maps match exact symbols only;
  raw-keyed maps match canonical feed symbols via the suffix probe. A raw
  symbol looked up against a canonical-keyed map falls back (never happens in
  the runner — feeds are canonical).
- **wave4 int vs float mults:** WAVE4_BARRIERS uses ints (3..6, 4..12) so
  combo keys render `s3x12` (vs wave3's float `s4.0x3.2`); msgspec accepts
  ints for the float `stop_sigma_mult` fields. Cosmetic only.
- **`--warmup-bars` default 100 changes legacy-space behavior** (was implicit
  0): intentional — protocol parity is the point; legacy spaces can pass
  `--warmup-bars 0` for the old cold-start rankings.
- **Relax is by-row, not by family-key:** the top-K-by-WR ∪ top-K-by-PF
  union picks individual combos; "family" grouping (e.g. sharing sigma
  shape) is a T9 presentation concern — the CSV carries every knob column so
  families are recoverable.
- Not touched per contract: entropy_accuracy_btc15m.py, runner.py,
  risk/manager.py, portfolio.py, engine/*, UI, walk-forward/grid scripts
  (their `vote_mode=c["vote_mode"]` str construction stays valid under the
  union).