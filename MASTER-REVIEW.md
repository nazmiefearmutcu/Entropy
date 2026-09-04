# MASTER-REVIEW — Entropy bot (nazmiefearmutcu/Entropy)

> **FIX WAVE 2 (2026-09-04, same session):** also FIXED —
> H1 entry-grace ported INTO the bot (`RiskOverrides.entry_grace_bars=3` shipped default,
> `RiskManager.check_exits` native suppression with harness-identical arithmetic incl.
> stop-first-while-suppressed, runner plumbing + hot-apply `set_entry_grace`; the harness
> neutralizes the cfg field so its own argument stays the single authority in evidence runs;
> 8 new tests in `tests/bot/test_entry_grace_native.py`). **Byte-identity proven**: the
> flagship q_s20 artifact (ETH-30d) replays IDENTICAL under the new code — 10/10 metrics
> and 30/30 trades equal to the pre-change report; new `trade_weighted_return_pct` shows
> +0.57% vs the +1.79% equity headline on that window (H8 quantified on real evidence).
> H6 per-process auth token: sidecar generates `secrets.token_urlsafe(24)`, prints
> `TOKEN=` before `PORT=`; all `/api/*` require `x-sidecar-token` (401 otherwise);
> WS checks Origin allowlist (browser clients) + `?token=` (all clients); Tauri lib.rs
> captures and injects `__SIDECAR_TOKEN__` (cargo check clean); frontend sends it on
> every request + WS handshake (localStorage dev escape hatch). Tokenless sidecars keep
> legacy open behaviour (back-compat); 1 new test.
> H10 dict vote_mode UI guards: textual Bot tab renders a read-only "per-symbol map"
> note instead of the crash-prone Select, Reset skips it, Save carries the map verbatim
> (test added); React BotSettings renders a warning panel for maps; contract.ts types
> vote_mode as `string | Record<string,string>`.
> Docs truth: README vote_mode default + Round-2 s20 shipped-defaults section (Round-1
> narrative marked historical); PROJECT.md grid_search command gets its required
> --klines/--out, .venv/Scripts paths, walk-forward legacy-mismatch warning, "746 tests"
> figure corrected to a live command, H-section 60d dagger footnote, gate return-basis note.
> Perf flake: test_engine_throughput bound 100k → 30k (regression-only, load-margin
> comment; the engine measures ~500k-1M idle).
> RESULT: full suite **all green** (including the perf test), sidecar **64/64**, cargo check clean.
> Still deliberately NOT changed (strategy/owner decisions): H2 harness risk-gate cadence,
> H12 strategy knobs (max_daily_loss 40→5, sizing), CSP policy, zombie-sidecar Job Objects,
> warmup-gap bar-counter semantics.

**Date:** 2026-09-04 · **HEAD:** `d0f46b9` (branch base Round-1 ship `b91e644`, Round-2 ship s20)
**Method:** 14 parallel deep-review subagents (GLM 5.3 Flash), each with a dedicated lens (harness integrity, trade lifecycle, evidence chain, real-money path, market-data path, consensus/config, settings round-trip, calibration/costs, sidecar IPC, tail-risk quant, test quality, docs/deps/tauri/secrets, Textual UI, React frontend). Several findings were **verified by live probe/reproduction**, not just code reading. A planned 158-task out-of-process swarm was aborted: the opencode-go provider weekly quota is exhausted (resets in ~3 days); this document consolidates the same coverage.
**Suite state at HEAD:** 892 tests collected; ~37 failed — 26× plotext Windows pre-epoch crash (see C1), 2× POSIX-only clock (H4), 1× ccxt missing from venv (B1), a few genuine textual-drift, 1× known `test_engine_throughput` perf flake. **All bot/strategy/gate/harness (evidence-creating) suites are green.**

---

## Executive verdict

The shipped s20 config and its evidence are **internally coherent** (cfg parity gate↔harness verified field-by-field, Wilson algebra reproduced by hand, closed-trade accounting exact, no lookahead in the signal path). But the review found **(a)** three verified-fatal defects that kill the *live dashboard on Windows*, **(b)** a structural evidence-to-bot gap (grace, risk-gates, warmup exist only in the harness; the "rolling" gate never rolls due to a cache bug), **(c)** a return-accounting integrity issue (honest ETH-60d return is **−0.43%**, not +1.62%), and **(d)** the live-money layer is unbuilt behind a `NotImplementedError` — with two paths (PUT hot-apply, unauthenticated IPC) that become money-critical the moment it is wired.

---

## CRITICAL (verified, user-visible today)

**C1. Windows + plotext 5.3.2: first chart render kills the whole TUI.**
`src/entropy/ui/widgets/charts.py:179,236` + `plotext/_date.py:56`. Sub-day axis formats (`H:M`) make plotext `strptime` default to year 1900 → `datetime.fromtimestamp(<negative>)` → `OSError [Errno 22]` on Windows, raised inside Textual's render pass → app exit. Minimal repro confirmed. This is the root cause of 26/28 `tests/ui` failures. Fix: pin/ban plotext 5.3.2 on Windows, monkeypatch `time_to_string` to epoch-based construction, and wrap `replot()` defensively.

**C2. One bad tick in the drain worker exits the entire app.**
`src/entropy/ui/app.py:790-811` (`run_drain`) — the 10 Hz tape consumer has no per-record try/except; Textual workers are `exit_on_error=True` by default (verified: injected `ZeroDivisionError` on a 0-price tick terminates the app). Same hole: unguarded `await self._equity.run()` at `app.py:898`, and sidecar `stream.py:434-439` (`engine.on_trade` unguarded — kills tape AND the bot's stop management silently). Fix: per-record try/except + continue, supervise the drain task.

**C3. `apply_config` hot-swaps the executor of a RUNNING bot, contradicting its own docstring.**
`src/entropy/bot/runner.py:285,299` vs docstring `:249-254` ("mode is NOT re-read"). Probed: one `PUT /api/settings {"bot":{"mode":"live","live":{"acknowledged_risk":true,...}}}` (validate only requires the ack, `config.py:406`) → `PaperExecutor` → `LiveExecutor` on a running bot. Consequences today: every order — **including mechanical stop/TP/time-stop exits** — hits `LiveExecutor.submit` → raise → `_execute` swallows (`runner.py:162-176`); positions run stopless forever, `live_blocked` ledger events spam per tick, ledger keeps its original `mode:"paper"` label, and `snapshot().mode` reports "live". Plaintext API keys from the same PUT are persisted to `~/.entropy/settings.json`. Fix: reject mode changes in `apply_config` ("requires restart"), never swap the executor with positions open, rate-limit `live_blocked` events. **No test pins mode in `apply_config` at all.**

---

## HIGH

**H1. Entry-grace exists only in the harness — the bot cannot reproduce its own evidence.**
`scripts/entropy_accuracy_btc15m.py:290-314` monkeypatches `runner.risk.check_exits`; `grep entry_grace src/` = 0 hits. All published numbers (grace=3) suppress mechanical stops for entry bar + 2; the live bot takes those stop-outs. "default 3 = shipped" is true only for harness defaults.

**H2. Risk-layer entry gates (vol floor + 3% deviation guard) are structurally dead in the harness, active live.**
Harness feeds exactly 4 ticks/bar; both gates require `_MIN_WINDOW_TICKS = 5` (`risk/manager.py:20,244,258`). The WR 75-81% evidence was measured with these gates inert; a live tick stream activates them — the measured trade population is not the deployable one. Also: live `warmup()` seeds strategies only (`runner.py:370-420`), so the risk layer fails open for the first `vol_window_s` after every start.

**H3. The rolling gate never rolls: klines cache validates only `bars`, not `end_ms`/symbol.**
`scripts/entropy_accuracy_btc15m.py:102-105`; gate default `--out /tmp/entropy_wr_gate` (`entropy_wr_gate.py:207`). A scheduled gate run returns day-one's window forever and exits 0 on the stale verdict; a `--end-date X` run poisons later `now` runs; a BTC run poisons a later ETH run in the same dir (the exact BTC-for-ETH invalidation class already hit once). Fix: compare `meta["end_ms"]` and store/compare `raw`.

**H4. POSIX-only clock: all crypto kline warmup fails on Windows.**
`src/entropy/feeds/warmup.py:69` — `time.clock_gettime_ns(time.CLOCK_REALTIME)` does not exist on Windows CPython (verified; also the root cause of 6 undocumented `tests/feeds/test_warmup.py` failures). `BotRunner.warmup` catches it (`runner.py:407`) → bot runs **unwarmed every start** (~8.75h of no consensus signals at 15m); focus-chart warmup fails likewise. Fix: `time.time_ns()` or crocodile's `now_ns()` (the venv crocodile already carries the fallback patch — entropy's own call site was missed).

**H5. Multi-symbol warmup seeds EVERY symbol with ONE symbol's history — and tests pin it as intended.**
`consensus.py:411-419` + `runner.py:391,415-417`; `tests/bot/test_vote_mode_per_symbol.py:232` asserts equal closes across symbols as the correct state. With `symbols=(BTC,ETH)`, ETH evaluates indicators/entry-sigma over a BTC→ETH price discontinuity for up to 512 bars (~5 days). Dormant in shipped single-symbol config; a trap cemented by tests.

**H6. IPC has no authentication and no Origin check — any webpage can drive the bot.**
`sidecar/app.py:32-36,122-133,269-301`. CORS blocks only preflighted requests; `POST /api/bot/start|stop|pause|halt`, `/api/command`, `PUT /api/settings` are simple requests (drive-by `fetch` executes them); `/ws/live` ignores Origin entirely (any page can exfiltrate equity/positions at 10 Hz). Becomes CRITICAL the moment live routing is wired. Fix: per-process token printed beside `PORT=`, required on every route + WS handshake; validate Origin on WS.

**H7. `stop_bot` strands open positions with no exit management.**
`stream.py:527-532` + drain gate `:441` — stop stops `check_exits` from ever running; open positions lose stops/TP/time-stop silently (contrast: pause deliberately keeps exits; halt liquidates). Untested with open positions.

**H8. Return accounting integrity: WR/PF use clamped fills, return uses unclamped extreme fills.**
`entropy_accuracy_btc15m.py:446-454` re-prices mechanical exits for the WR/PF walk only; `total_return_pct` comes from portfolio equity closed at raw extreme fills (`runner.py:208`). Deterministic re-run from on-disk artifacts: ETH-30d +1.79% headline vs **+0.57%** honest; BTC-30d +1.53% vs **+0.72%**; **ETH-60d +1.62% vs −0.43%**; and the gate's `ret>=0` leg (`entropy_wr_gate.py:241`) consumes the optimistic number while PF uses the pessimistic one. Warmup PnL also leaks into `total_return_pct` (harness `:384-389,:516`) — bounded (~±0.15%, tail −0.5%), safe for the shipped margins, but can flip marginal scheduled-gate windows.

**H9. Frontend chart-type control is broken against the real sidecar.**
`ChartPanel.tsx:14-17` / `SettingsDrawer.tsx:252-255` send `"candles"`; sidecar vocabulary is `("candlestick","line")` (`contract.py:33`). Snapshot echoes `"candlestick"` → no active segment; clicking "Candles" is always rejected. `mock.ts` accepts anything, so tests are blind. Fix: standardize on `candlestick`.

**H10. Dict `vote_mode` bricks both UIs (and the escape hatch).**
Textual: `modals.py:329-331,466,512` — dict value → `Select` blanks, Reset raises `TypeError: unhashable type: 'dict'` (verified), Save writes `str(Select.NULL)` → refused by validate → Bot tab unusable until the user picks a mode, which **silently flattens the per-symbol map** to one global string. React: `BotSettings.tsx:390-406` renders a raw object as a React child → invariant crash, and there is **no error boundary anywhere** → white screen. Dormant until a dict map is written to settings (sweep tooling or crafted PUT are realistic writers).

**H11. Secrets at rest and on the wire.**
`LiveConfig.api_key/api_secret` persisted plaintext to `~/.entropy/settings.json` (`settings.py:69-84`), and `GET /api/settings` echoes them unredacted (`app.py:157-162`) into webview JS (no component even renders them). Nothing on disk exists yet on this machine; `LiveExecutor` is inert. Fix: env/keyring only; redact GET.

**H12. Tail-risk profile: short-vol in trend-following clothing.** (quant agent, artifacts re-analyzed)
Realized loss bound = the 192-bar time stop (mean −2.29%, worst −5.46% of notional in the 60d window), not the 20σ stop (0/30 ETH, 2/36 BTC fires). Empirical breakeven WR **70.7%** vs claimed 76.7% ± 14.6pp (Wilson 23/30 = [59.1%, 88.2%] — LB below the directive); P(X≥23 | n=30, p=0.707) = 31% — the flagship sample is fully consistent with a breakeven process. A 5% stop-out or gap-out rate pushes breakeven to 80.9-84.1% → book negative. **Nothing automatic flattens**: daily kill switch blocks entries only, `reset_day()` un-halts at midnight with the position open, the only flatten is a manual UI breaker. Stop width is unbounded above (20σ ranges 0.52%-22.9% with entry-σ; hard clamp only at 50%). Feed outage while positioned = no stop, no TP, no time stop, unbounded.

---

## MEDIUM (grouped)

**Trading-path logic**
- Out-of-order tick commits the current bar prematurely and injects a stale close (`consensus.py:461-480`); WS outages compress bar-counting (time-stops/cooldowns stretch in wall-clock terms) — no gap marker (`consensus.py:463-476`).
- Engine `0.0` price passes the finite guard and divides (`engine.py:180`, `windows.py:65`).
- Startup cost gate judges the percent stop even in sigma mode (`config.py:480`) — can hard-refuse configs whose real 20σ stop is fine (FROSTY + percent mode is untradeable on crypto spot: 26/50bps = 0.52 > 0.5).
- `classify_symbol` fails OPEN to the cheapest regime (2bps equity) for unknown symbology (`BTCUSD`, `BTCPERP`, `BTC-GBP`) (`costs.py:71-73,91`); the entire calibration pipeline runs at flat 2bps with gates off (`calibration.py` — no caller passes `market_costs`), so its "optimal parameters" optimize the wrong objective.
- Cost gates pass cost-dead geometry: at the σ floor, net win +18bps vs net loss −246bps → breakeven WR 93.2% if stops fired. No payoff-aware gate exists.
- Daily-loss kill switch: blocks entries only, never flattens; `halted` self-clears at UTC midnight (`manager.py:104-110`, `runner.py:453-461`); cap 40% is unreachable at 40% max exposure; no `daily_halt` ledger event.
- Circuit breaker has no reset path; `entry_grace` also suppresses exactly the highest-risk first 3 bars (harness).
- `walk_forward.py` would run today but silently measures sigma 20/4 (RiskOverrides defaults) instead of its documented fixed 1.5/2.0, and a `ship_default` that is not s20 — any fold output would be mislabeled evidence (`entropy_walk_forward.py:50-62,131-134`).
- Live executor gaps (when wired): no reconciliation at startup (sizing on fictional equity), stops are software-only on the same single-threaded loop, no idempotency/client-order-id, no partial fills/min-notional/lot rounding, `_execute` catches only 2 exception types (any submit failure kills the bot), equity trades on the SIMULATED tape even in live mode (`runner.py:108,478`).

**Settings/UI/IPC**
- PUT storm: every keystroke/slider tick fires a full `PUT /api/settings` + disk write + GET reload (`BotSettings.tsx:674-677,87`); concurrent PUTs race server-side → lost updates; optimistic draft not rolled back on rejection (`SettingsDrawer.tsx:82-96`).
- NumberField/number inputs commit `0` when cleared (`controls.tsx:359-366`): `fee_bps→0` = free-trading cost model accepted by validate; `per_trade_pct=0`/`max_concurrent=0` = silent never-trading bot; `bar_s→0` flips timeframe-follow semantics.
- Contract drift: `contract.ts` omits the shipped s20 knobs (stop_mode, sigma mults, max_hold_bars, long_only, risk_trail_pct) — GUI's advanced panel gives a wrong mental model; `mock.ts` uses impossible wire values (`exit_mode:'signal'`, `normalize:'weights'`) and `NORMALIZE_HELP` keys match no real value.
- `config.error` written but never rendered (App.tsx:110,119) — unparseable frames / meta failure fully invisible.
- Theme validation crash at boot on unknown persisted theme (`app.py:281` + textual 8.2.7 `_validate_theme`); focus switch leaves the previous symbol's candles+title until first tick (`charts.py:166-168`); `_error_text` is last-write-wins (real failures clobbered, verified in test run); `_apply_settings` unguarded multi-step transaction can wedge `_saving=True`.
- Sidecar: depth fetch 10Hz × per WS client without TTL cache (`stream.py:662-666`); sync ledger writes on the event loop (hot under C3); orphaned sidecars (dev kills `uv`, packaged kills onefile bootloader; Windows needs Job Object / `taskkill /T`); `PORT=` printed before bind (TOCTOU + no readiness check); unbounded request bodies; `app.watchlist_path`/`trade_csv_path` = arbitrary same-user file clobber/create via IPC; CORS allowlist missing the Windows `http://tauri.localhost` origin (the app's own release build breaks); CSP null in tauri.conf.json.
- Textual widgets: per-symbol caches never evicted; clean feed exit silent (no reconnect); stale `_equity_source_resolved` after live feed death.

**Evidence/docs/process**
- Gate verdict is point-estimate only; ETH PASS CI lower bound is 59.1% (< 60% directive). Wilson math itself verified correct; CI printed but consumed by no criterion.
- H-section of PROJECT.md cites the train-overlapped 60d row without the dagger footnote (footnote exists only in the Round-2 section).
- Global ship of an ETH-tuned config rests on one 30d BTC window; no BTC 60d/90d rows; future BTC 60d checks will hit the same train overlap.
- Sweep relax is shard-local (K stamped per shard); by-PF family can admit 0-trade rows.
- README is stale: says `vote_mode` defaults `adaptive` (it is `trend`), and its "shipped defaults" narrative describes the pre-H ship (direction_bars=0, 1.5%/1.2% SL/TP) — PROJECT.md is current, README is not.
- PROJECT.md Reproduce block: `.venv/bin/python` on Windows; `entropy_grid_search.py` command missing required `--klines`/`--out` (errors out); "746 tests" claim wrong (892 collected); `progress.md` "Last visited 2026-08-03" despite 09-03 sections.
- Deps/tooling: dev venv stale vs `uv.lock` (ccxt + pytest-timeout missing → 1 failing test + zero per-test timeout enforcement); `crocodile` git dep without `rev` in pyproject (lock-only reproducibility); `textual>=0.79` floor is fiction (app needs ≥0.86 APIs, lock resolves 8.2.7) with no CI to catch it; 27 of the UI failures are the plotext Windows bug, not textual drift.
- Git hygiene: `.superpowers/` not gitignored (28 untracked files, one tracked-modified — half-in/half-out); `scripts/entropy_multi_symbol.py` is untracked real code (loss risk).
- Tests: `test_runner.py:171-186` vacuous-pass (assertions inside `if pos is not None`); module-scope `BotConfig` defeats the conftest isolation fixture (`test_hot_apply.py:11` → writes `./entropy_trades.csv` in CWD); trail-mode + 192-bar time-stop combination never behavior-tested (only hold-mode); hardcoded `/tmp` paths in harness tests; no conftest-level socket guard.
- Frontend: FocusChart pan/zoom resets every new bar; `?port=` override accepts 0/garbage; `api.query` has no timeout; TopBar shares one `detail` between EQ/CR chips.

---

## LOW (selected)
Zero-loss PF fallback returns dollars not a ratio (`:488`); per-trade Sharpe annualized with √252 mislabeled; `costs_paid` overstated vs WR math; `hold_bars` hardcodes 900s; `--skip-fetch` doesn't skip; empty-klines IndexError; "entry bar's RMS" docstring wrong (it's trailing 20-bar RMS); `simulate(trade_csv=)` dead param; warmup trade counter off-by-one at boundary; legacy pinning leaks normalize/min_participation globally (no-op under shipped `normalize="total"`); direction filter warmup hole (35-40 bars); `exit_mode="trail"` + `trail_pct=0` unvalidated churn machine; unknown-symbol canon warns falsely for canonical keys; settings `version` field never checked, no migration hook; two-writer `entropy_trades.csv` with per-process LIFO registry; ledger writes not fsync'd, first failure logged at DEBUG only; `Portfolio.open` silently overwrites same-symbol position; benchmark imports DummyLedger from calibration; breadth first snapshot always "accelerating".

---

## Quantified tail-risk summary (s20)
- Loss bound = time stop: 192 bars ≈ 48h adverse drift (observed mean −2.29%, max −5.46% of notional); stop fills at the tripping tick (gap + ~23bps), level clamp exists only in the harness.
- Breakeven WR: 70.7% empirical; 76.7% at 5% median-σ stop incidence; 80.9-84.1% with 5% stop/gap incidence; pure stop-vs-TP geometry needs 89.6-91.0%.
- Evidence: 23/30 → Wilson [59.1%, 88.2%]; n=35 needed for LB>60% at this pace; P(≥23|p=0.707)=31%. 60d split: WR stable (78.1/75.0) but payoff is not (loss 3.6× win); August profit = 139% from two vol-spike entries; honest 60d return −0.43%.
- Recommendation ranking: (1) flatten on kill switch (~10 LOC), (2) honest return accounting + gate on it (~15 LOC; flips 60d to FAIL), (3) σ-mode stop clamp [1%, 8%] (~5 LOC), (4) feed-staleness watchdog → flatten/alert (~20 LOC), (5) live stop fills at level, (6) config levers: grace 3→0, max_daily_loss 40→5, risk-unit sizing; (7) pre-registered halt rule on fresh-window PF<1.0; (8) publish both accounting systems.

## Verified non-issues (do not re-flag)
- Gate↔harness cfg parity byte-identical (13+13 fields, test-enforced end-to-end); Wilson algebra exact (all 4 CIs reproduced by hand); trades≥20 closed-only; 30d window = exactly 2880 closed bars.
- Harness internals: direction-aware stop-first intrabar ordering correct and pessimistic both sides; no double fee/slip count; level clamp worse-of semantics; zero-PnL-as-loss; warmup chaining produces identical post-boundary signals modulo the two documented boundary events; `max_hold_bars=192` fires at bar N+192's open exactly (no off-by-one); same-bar stop+TP stop-first in both layers; gap-through-stop keeps the worse (actual) price; `risk_trail_pct=0.0` proven inert (`_ratchet_stop` early-return) and correct when enabled; sigma anchored once at open from fill price incl. entry slip; no lookahead in signal path (completed bars only, entry fills at current tick).
- Live orders are impossible today (`NotImplementedError` is the only thing between the code and real money — and it holds); `LIVE_WARNING` prints even piped; rejected entries re-arm strategy state; paper/live share one sizing/fee path; PnL sign conventions consistent.
- No hardcoded secrets anywhere (incl. git history); no subprocess/eval/pickle/shell=True; no innerHTML sinks; sidecar has no command injection and no path traversal via symbol params; msgspec rejects NaN/∞ and unknown keys; deep-merge preserves untouched nested state (dict vote_mode survives partial saves from the GUI); `starting_cash` genuinely not hot-rebased; TUI threading clean (single-loop consumers); textual 8.2.7 modal/binding behaviors verified inert; WS client backoff/parse-error handling sound; store memory bounded; React key hygiene good; runner pause semantics (blocks entries, never exits) correct and tested; halt-with-open-positions covered at runner level.
- Shipped defaults in code == documented s20 exactly (`test_default_config_is_shipped_h` pins it); no settings.json exists on this machine (nothing is trading; effective config = code defaults).

## Prioritized fix list
1. `uv sync` (root+sidecar) + fix `warmup.py:69` clock call (one line) — unbreaks suite and warmup on Windows.
2. TUI C1+C2: plotext pin/monkeypatch + defensive replot; per-record try/except in `run_drain` and sidecar `_drain` + worker supervision.
3. `apply_config`: honor the docstring (refuse mode change; never swap executor with positions open); add mode-change tests; redact `GET /api/settings`; stop persisting raw secrets.
4. Gate/harness: cache key += end_ms+raw (H3); report honest (clamped) return and gate on it (H8); subtract warmup PnL from the gated return; document harness-only knobs (grace, risk-gate cadence) next to the evidence they inflate.
5. Sidecar: per-process auth token + WS Origin check; `stop_bot` flatten-or-warn; IPC body cap; Windows CORS origin; Job-Object kill; PORT after bind.
6. Tail-risk package: flatten-on-kill-switch, σ-stop clamp, staleness watchdog, grace 3→0, max_daily_loss 40→5.
7. Frontend: `candlestick` vocabulary, commit-on-blur/debounce PUTs, single-flight mutations, error boundary + dict vote_mode guard, NumberField empty→no-change, contract.ts s20 fields (read-only at minimum), regenerate mock from sidecar constants.
8. Docs/repo: README sync, PROJECT.md command/number fixes + dagger in H-section, `.superpowers/` policy + commit `entropy_multi_symbol.py`, crocodile `rev` pin, textual bound, minimal CI (would have caught C1/H4/B1 immediately), un-break `walk_forward.py` or mark legacy.

*14 agent reports were consolidated into this document; per-agent raw outputs lived in the session transcript (the out-of-process report files were lost with the aborted swarm).*
