# T3 Brief — accuracy levers: long_only, sigma-scaled barriers, time stop

Repo: C:\Users\Kullanıcı\Entropy (Windows). Gate: `.venv/Scripts/python -m pytest tests/bot tests/strategy -q`
must be green (288 tests currently). Design: docs/superpowers/specs/2026-09-03-winrate-over-60-design.md.
Prior context you need:
- T1 landed: consensus trail peak reset (commits 9aee08e..c313e81). T2 landed: harness
  resolves intrabar barriers stop-first per direction and clamps mechanical fills to
  barrier levels captured at open (`_BarrierLedger` in scripts/entropy_accuracy_btc15m.py).
  Ledger invariant: "barriers are immutable after open" — T3 must PRESERVE this (see 3).
- Measured (honest harness, 30d OOS ending 2026-09-02): total WR 58.1% (31 trades),
  LONG-ONLY WR 63.2% (19 trades) — shorts drag accuracy and are not executable on
  Binance spot live anyway.

Touch ONLY: src/entropy/bot/strategies/consensus.py, src/entropy/bot/signals.py,
src/entropy/bot/config.py, src/entropy/bot/risk/manager.py, src/entropy/bot/runner.py,
scripts/entropy_accuracy_btc15m.py (flag plumbing only), and tests
(tests/bot/test_consensus.py, tests/bot/test_new_features.py or new test files).
Commit in small conventional commits. Do not push.

## 1. `long_only` flag (ConsensusConfig, default False)
When True, the strategy never emits ENTER_SHORT: in the entry path, if the qualifying
score sign is negative, skip the entry entirely (reset streak, do not enter). Document
that this matches spot-deployable reality. Plumb through:
- BotConfig.consensus (ConsensusConfig dataclass field).
- scripts/entropy_accuracy_btc15m.py: `--long-only` flag (default off) setting it.
Tests: long_only=True never emits ENTER_SHORT (short-qualifying score bar), longs
still work; default False preserves existing behavior exactly.

## 2. Time stop (ConsensusConfig `max_hold_bars`, default 0 = off)
When max_hold_bars > 0 and the position's `st.bars_in_trade >= max_hold_bars`, emit an
EXIT (respecting nothing else — a time stop overrides trail/score/trend checks but must
still wait for `min_hold_bars`? NO: a time stop is independent; if max_hold_bars <
min_hold_bars the config is contradictory — validate max_hold_bars == 0 or >=
min_hold_bars at construction, raise ValueError otherwise). Exit reason/signal follows
the existing EXIT path. Add `--max-hold-bars` to the accuracy script.
Tests: time stop fires at the configured bar; off by default; validation raises.

## 3. Sigma-scaled barriers (RiskOverrides, default percent = unchanged)
Add to RiskOverrides: `stop_mode: str = "percent"` ("percent" | "sigma"),
`stop_sigma_mult: float = 1.5`, `tp_sigma_mult: float = 1.2` (defaults mirror the
shipped 1.5%/1.2% shape).
Mechanism:
- Signal (src/entropy/bot/signals.py) gains an optional field `sigma: float | None =
  None` (per-bar RMS of returns, a fraction — e.g. 0.0011). ConsensusStrategy sets it
  on ENTRY signals from `self._bar_move_rms(closes)` at the entry bar (compute it
  there; do not reuse the cost-gate's cached value blindly — same number, but compute
  at the entry branch for clarity and set the cache too).
- BotRunner's position-open path (where `stop_tp_prices` is called): when
  risk_overrides.stop_mode == "sigma" and the entry signal carries sigma > 0, pass
  stop_pct = stop_sigma_mult * sigma * 100 and tp_pct = tp_sigma_mult * sigma * 100
  into the existing percent barrier computation (i.e., convert sigma-mults to the
  same units the percent path consumes). When sigma is None/0 or mode is "percent",
  behave exactly as today.
- Validation: stop_mode in {"percent","sigma"} else ValueError; mults > 0.
- HONESTY INVARIANT (ledger T1T2-M3): barriers stay immutable after open (no
  re-anchoring). The harness `_BarrierLedger` capture-at-open therefore remains valid;
  verify this by reading the harness code and note it in your report. Do NOT change
  the harness beyond the CLI flag.
Tests: sigma mode produces wider/tighter stops proportional to sigma (construct a
position with known sigma and assert barrier prices); percent mode unchanged; missing
sigma falls back to percent; runner plumbing passes sigma from entry signal.

## 4. Accuracy script flag plumbing
Add `--long-only`, `--max-hold-bars`, `--stop-mode`, `--stop-sigma-mult`,
`--tp-sigma-mult` to scripts/entropy_accuracy_btc15m.py, wired into the same
BotConfig/ConsensusConfig/RiskOverrides it already builds, echoed in report["config"].
Defaults preserve current behavior exactly (long_only False, max_hold_bars 0,
stop_mode percent).

## Constraints
- Full bot/strategy suite green; all new defaults are no-ops (existing tests must pass
  UNMODIFIED except where they assert constructor signatures).
- No new dependencies. No UI changes.
- Full report: append to .superpowers/sdd/t3-report.md (status DONE |
  DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED; commits; test summary; concerns;
  per-file change list).
- Final message: ONLY status, commit list, one-line test summary, concerns.
