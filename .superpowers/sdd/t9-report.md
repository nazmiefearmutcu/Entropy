# T9 report — ship Round-2 config s20 (global) + gate parity + docs

## Shipped values (file:line)

`src/entropy/bot/config.py`:
- `ConsensusConfig.vote_mode` default `"adaptive"` → `"trend"` (~line 45) + comment
  (global — per-symbol map stays in code for future use).
- `ConsensusConfig.max_hold_bars` `96` → `192` (~line 90) + comment.
- `RiskOverrides.stop_sigma_mult` `5.0` → `20.0` (~line 192) + comment
  (tp_sigma_mult 4.0, direction_bars 20, long_only True unchanged;
  risk_trail_pct ships at 0.0, unchanged).
- `validate()`/`warnings()` untouched (trend is in VOTE_MODES; no new rule needed).

`scripts/entropy_accuracy_btc15m.py` (CLI defaults only, NO behavior change):
- `--vote-mode` default `adaptive` → `trend`; `--stop-sigma-mult` `5.0` → `20.0`;
  `--max-hold-bars` `96` → `192`; `--entry-grace-bars` `0` → `3`
  (`simulate()` signature default stays `0` — the T6 byte-identical guarantee
  for explicit grace=0 runs). Module + legacy-repro docstrings updated.

`scripts/entropy_wr_gate.py`:
- `build_ship_default_cfg` gains keyword-only `stop_sigma_mult=20.0`,
  `tp_sigma_mult=4.0`, `max_hold_bars=192`, `direction_bars=20`
  (existing `vote_mode` default `adaptive` → `trend`, `risk_trail_pct=0.0`
  unchanged); body wires all six into ConsensusConfig/RiskOverrides.
- New CLI flags `--stop-sigma-mult` (20.0), `--tp-sigma-mult` (4.0),
  `--max-hold-bars` (192), `--direction-bars` (20); `--vote-mode` default →
  `trend`, `--entry-grace-bars` default `0` → `3`. main() forwards all to the
  builder; levers echo + report.json `config` carry all seven knobs.

## Gate parity method

- T9a pattern followed: builder defaults == harness CLI defaults, asserted by
  pinning both sides. Extended for T9: the 1:1 contract test
  (`test_gate_cli_defaults_match_harness_cli_defaults`) runs BOTH mains with
  fetch/simulate stubbed and zero knob flags, then asserts
  `gate_cfg.consensus == harness_cfg.consensus`,
  `gate_cfg.risk_overrides == harness_cfg.risk_overrides`, and both forward
  `entry_grace_bars=3` to simulate(). The pre-T9a parity test and the
  builder-signature-default test were updated to s20 values
  (trend / 192 / 20 / 20.0 / 4.0 / grace 3).
- Live proof: the gate's shipped-default run and the harness's bare-default
  run on the same window produce identical trade counts and metrics
  (ETH: 30t/76.7%/1.35 both; BTC: 37t/81.1%/1.55 both).

## Verification numbers (network, no overrides, window ~2026-08-04 15:15 → 2026-09-03 14:59 UTC, 2879 evaluated bars + 100 warmup)

| Run | WR | trades | ret | PF | maxDD | exits | gate |
|---|---|---|---|---|---|---|---|
| ETH 30d accuracy | 76.7% | 30 | +1.77% | 1.35 | 0.77% | 23 TP / 7 close / 0 stop | — |
| ETH 30d gate | 76.7% | 30 | +1.77% | 1.35 | — | — | PASS 4/4, CI [59.1%, 88.2%] |
| BTC 30d accuracy | 81.1% | 37 | +1.52% | 1.55 | 0.59% | 30 TP / 5 close / 2 stop | — |
| BTC 30d gate | 81.1% | 37 | +1.52% | 1.55 | — | — | PASS 4/4, CI [65.8%, 90.5%] |

vs the brief's orchestrator numbers (ETH 76.7%/30t/1.35/+1.79%; BTC 80.6%/
36t/1.57/+1.53%): within window-shift tolerance (my window ends ~15:00 UTC vs
theirs; BTC picked up one extra trade, 37 vs 36). No tuning was done — the
gate passed on framing as-shipped. Full logs: C:/tmp/entropy_t9/{eth_30d,
btc_30d,gate_eth,gate_btc}/report.json.

## Docs locations

- PROJECT.md: new "Round 2 — ETH fixed (2026-09-03, shipped)" section
  (selection story, evidence table + post-ship CIs, standing wide-stop risk
  note, s20 + H-shape + legacy reproduce commands, K=6 documentation,
  deferred items) + "Reproduce (Round-2 s20 shipped defaults)" block.
- progress.md: "Round 2 — ETH fixed" close-out checklist.
- Design doc Round-2 section left as-is (status line says "in progress" there;
  the shipped record lives in PROJECT.md per the brief's doc split).

## Test count

- `pytest tests/bot tests/strategy tests/engine -q`: 503 collected
  (502 baseline + 1 net new end-to-end parity test; 2 T9a tests renamed to
  shipped-s20 names), 502 passed, 1 failed = `test_engine_throughput`
  (42k vs 100k ticks/s) — the known environmental perf flake the brief says
  to ignore; unrelated (no engine files touched).
- Mechanics tests keep pinning legacy/H shapes explicitly (T5 pattern):
  `test_accuracy_harness._cfg` (percent 1.5/1.2, db 0, hold 0, shorts on),
  `test_entry_grace._cfg`, and now `test_sigma_barriers._run_one_entry`
  (explicit 5.0/4.0 so the anchoring-math assertions are ship-default-proof).
  Sweep-space tests (`test_wr_sweep_space`, `test_wr_sweep_round2`) untouched —
  they pin combo-space values, not ship defaults.

## Concerns

- The 20σ stop makes the stop leg nearly vestigial (ETH 30d: 0 stops); the
  edge is TP + 192-bar time-stop + score exits on a global trend filter. If
  the regime turns, losses arrive as slow TP-misses/time-stop bleeds, not
  capped stops — the 60d PF 0.90 is the early warning. The gate is the
  monitor; do not widen the stop further without an OOS battery.
- `test_default_config_is_shipped_h` keeps its T5 name (history) while pinning
  s20 values — noted in its docstring; a future rename is cosmetic.
- Gate `--risk-trail-pct` still accepts negatives (argparse unvalidated,
  RiskOverrides rejects downstream) — carried from T9a, matches harness CLI.

## Fix-wave note — Round-2 final review findings (docs only, no behavior change)

Spec: `.superpowers/sdd/final-r2-review.md` (Important #1–3 + one-liner minors).
No code, tests, or configs touched; no sweeps or network verifications re-run.

1. 60d overlap (Important #1): PROJECT.md Round-2 evidence table — `ETH 60d`
   row marked with † plus a footnote sentence under the table disclosing the
   07-05→08-03 train overlap (~30d train + ~30d true OOS, not pure OOS) and
   that all gate decisions rest on the clean 30d rows.
2. Stop-tuning disclosure (Important #2): PROJECT.md standing risk note gains
   one factual sentence — s20's 20σ stop was TUNED (ladder 8→20σ + f04
   refinement) on the same 30d ETH window it headlines on, so the 30d
   numbers are optimistic for the stop knob and s20 stays provisional until
   the rolling gate confirms on a fresh window.
3. Folds ruling (Important #3): recorded in the ledger T9 close-out (not run
   — out of scope for this wave): 30d/60d/7d battery + BTC regression +
   gate-on-both-symbols deemed sufficient ship signal; folds would cost
   another sweep-scale cycle; rolling gate is the backstop.
4. Minors (one-liners): design-doc Round-2 status line "in progress" →
   "shipped"; PROJECT.md shipped-config paragraph gains the grace-3
   asterisk (bare `BotConfig()` + `simulate()` default = grace 0; shipped
   behavior needs grace 3 via harness CLI / gate defaults). Review file
   `final-r2-review.md` left pristine per contract.
