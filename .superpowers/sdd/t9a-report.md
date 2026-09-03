# T9a report — rolling gate learns Round-2 levers (parity)

## What changed (file:line)

`scripts/entropy_wr_gate.py` ONLY (plus its tests):
- `build_ship_default_cfg` (line ~87): gains keyword-only params
  `vote_mode="adaptive"` → `ConsensusConfig.vote_mode` and
  `risk_trail_pct=0.0` → `RiskOverrides.risk_trail_pct`. Docstring keeps the
  1:1 contract statement and documents the new knobs; it also records that
  `entry_grace_bars` is NOT a cfg field (top-level `simulate()` kwarg,
  forwarded at the call site).
- `main()` arg parsing (line ~168): three new optional flags —
  `--vote-mode {adaptive,trend,mean_revert,legacy}` (default `adaptive`),
  `--risk-trail-pct FLOAT` (default `0.0`), `--entry-grace-bars INT`
  (default `0`). Defaults equal old gate behavior, so existing scheduled
  runs are byte-identical.
- `main()` call site: builder receives `vote_mode`/`risk_trail_pct`;
  `simulate()` receives `entry_grace_bars=args.entry_grace_bars`.
- Echo: console prints a `levers : vote_mode=…, risk_trail_pct=…,
  entry_grace_bars=…` line; `report.json` `config` block gains the same
  three keys alongside symbol/bars/warmup_bars/end_ms.
- `tests/bot/test_wr_gate.py`: 5 new tests (`TestRound2Levers` + helpers);
  all 19 pre-existing tests untouched and green.

## 1:1-contract verification (method + result)

With all new flags at defaults the gate cfg is field-identical to the
accuracy harness's CLI-default cfg:
- `test_builder_defaults_match_harness_cli_defaults` builds the gate cfg
  with defaults and asserts `consensus.vote_mode == "adaptive"`,
  `risk_overrides == RiskOverrides(..., risk_trail_pct=0.0)` (all H fields
  incl. stop_mode sigma 5.0/4.0), and — since `entry_grace_bars` lives on
  `simulate()`, not on cfg — that the harness `simulate()` signature default
  for `entry_grace_bars` is `0` (via `inspect.signature`), matching the
  gate's `--entry-grace-bars` default. Builder signature defaults are
  asserted the same way. PASS.
- `test_main_defaults_keep_old_behavior` runs `main()` end-to-end with the
  fetch/simulate boundary stubbed and no new flags: `simulate()` receives
  `entry_grace_bars=0`, cfg carries adaptive/0.0, echo shows defaults. PASS.

## Test list + count

- `tests/bot/test_wr_gate.py`: 24/24 green (19 existing + 5 new:
  builder-forwards, defaults-parity, main-forwards-and-echoes,
  main-defaults, invalid-vote-mode-rejected). No live fetches in tests —
  fetch/simulate are monkeypatched spies.
- Full required suite `pytest tests/bot tests/strategy tests/engine -q`:
  502 collected, 501 passed, 1 failed = `test_engine_throughput` (44k vs
  100k ticks/s) — the known environmental perf flake the brief says to
  ignore; unrelated to this change (gate-only diff, no engine files touched).

## Concerns

- `entry_grace_bars` asymmetry is by design (simulate kwarg, not cfg), but a
  future reader may expect it on `build_ship_default_cfg`; the docstring
  states this explicitly.
- `--risk-trail-pct` accepts any float (incl. negative, which
  `RiskOverrides` validation rejects downstream); argparse does not
  pre-validate. Matches harness CLI behavior (also unvalidated).
