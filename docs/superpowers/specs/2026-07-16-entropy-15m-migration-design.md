# Entropy — 15m Timeframe Migration, Settings Rebuild & Bug Hunt

**Date:** 2026-07-16
**Branch:** `feat/entropy-15m-migration`
**Status:** Approved (design), pending implementation plan

## 1. Purpose

Entropy is a Textual TUI market terminal (live crypto + simulated equities feeds, a
breadth/entropy scanner engine, candlestick/line charts, an auto-trading strategy, and a
separate trading-bot subsystem). Today the terminal operates on a **second-scale** timeframe:
1-second rolling candles and scanner windows of 30s/1m/5m/20m.

This project does three things:

1. **Migrate the operating timeframe from seconds to 15 minutes**, as a first-class,
   *selectable* config abstraction (default `15m`), so candles, scanner windows, momentum,
   breadth, and warmup all rescale coherently — not just the chart.
2. **Remove the "trade-frequency setting"** — concretely, the **Risk Management Mode**
   selector (Frosty/Medium/Extreme) and its confirmation screen — from the settings UI.
   The bot subsystem and the automatic trade mechanism are **kept intact**.
3. **Rebuild the Settings screen from scratch** into a clean, sectioned panel that includes
   the new timeframe selector and excludes the trade-frequency control.

Then a **detailed, subagent-driven bug hunt** verifies the whole thing.

## 2. Confirmed decisions

- **Timeframe scope:** full migration to a 15m-centric engine **plus** a timeframe selector in
  Settings. (Not chart-only; not hardcoded-without-selector.)
- **What to remove:** *only* the trade-frequency setting = **Risk Management Mode** selector +
  its `SettingsConfirmScreen`. Do **not** touch `src/entropy/bot/`, `_on_strategy` auto-trading,
  risk profiles, or the ledger.
- **Settings:** completely recreate the existing `SettingsScreen`.
- **15m scanner window set:** `15m / 1h / 4h / session` (user-approved).

## 3. Current-state map (what exists today)

- `src/entropy/ui/app.py`
  - `_CANDLE_INTERVAL_NS = _S` (1s candles), `_WARMUP_BARS = 24`, `_WARMUP_DT_NS = 60*_S`.
  - `_on_strategy()` auto-opens/closes LONG/SHORT and records to `entropy_trades.csv`. **Keep.**
- `src/entropy/config.py` — `EngineConfig` with `windows_ns = {30s,1m,5m,20m}`,
  `momentum_horizon_s=5.0`, `breadth_window_s=30`, `momentum_cooldown_ns=1e9`.
- `src/entropy/app.py` — `AppConfig` (theme, chart_type, show_volume, risk_profile, feeds, tps,
  symbols, engine).
- `src/entropy/engine/` — `engine.py`, `candles.py` (`CandleAggregator(interval_ns)`),
  `windows.py` (`MonotonicExtreme`, `SessionExtreme`, `MomentumHorizon`), `rate.py`
  (`RateMeter(window_s)`), `breadth.py`, `leaderboard.py`.
- `src/entropy/ui/widgets/modals.py` — `SettingsScreen`, `SettingsConfirmScreen`, `HelpScreen`,
  `ErrorScreen`. Settings currently exposes: theme, chart style, volume, **Risk Management Mode**,
  equities/crypto feed toggles, equity TPS, strategy symbols, spike/snapdrop thresholds.
- `src/entropy/bot/` — full trading-bot subsystem (paper + live scaffold, risk profiles,
  runner, ledger, dashboard). **Untouched by this project** except that it keeps consuming its
  own `BotConfig.risk_profile`.

## 4. Design

### 4.1 Timeframe abstraction

Introduce a single source of truth for timeframe-derived parameters.

**New module `src/entropy/engine/timeframe.py`:**

```python
class TimeframeSpec(msgspec.Struct, frozen=True):
    name: str                    # "15m"
    bar_ns: int                  # candle/bar interval in ns
    windows_ns: dict[str, int]   # scanner windows (new highs/lows)
    momentum_horizon_s: float    # momentum reference lookback
    breadth_window_s: int        # breadth rate window
    momentum_cooldown_ns: int    # per-symbol momentum event cooldown
    warmup_bars: int             # bars to synthesize on warmup
```

**`TIMEFRAMES` registry** (ordered), default key `"15m"`:

| name | bar | windows_ns | momentum_horizon_s | breadth_window_s | momentum_cooldown | warmup_bars |
|------|-----|-----------|--------------------|------------------|-------------------|-------------|
| 1m   | 1m  | 1m/5m/15m/session   | 30      | 60    | 30s   | 24 |
| 5m   | 5m  | 5m/15m/1h/session   | 150     | 300   | 150s  | 24 |
| **15m** | **15m** | **15m/1h/4h/session** | **450** | **900** | **450s** | **24** |
| 1h   | 1h  | 1h/4h/1d/session    | 1800    | 3600  | 1800s | 24 |
| 4h   | 4h  | 4h/1d/session       | 7200    | 14400 | 7200s | 24 |

- `session` = cumulative session high/low (already handled by `SessionExtreme`, no ns window).
- Exact horizon/cooldown/threshold numbers above are **defaults to be validated** during
  implementation (subagents tune with tests, mirroring how risk profiles were tuned). Structure
  is fixed; values may be refined and the table in this doc updated to match.

**Wiring:**
- `AppConfig` gains `timeframe: str = "15m"`.
- `EngineConfig` is *derived from* the active `TimeframeSpec` rather than hardcoding second-scale
  defaults. `EngineConfig._default_windows()` is replaced by the 15m default; construction from a
  `TimeframeSpec` via a helper (`EngineConfig.from_timeframe(spec)` or equivalent) keeps
  `msgspec` frozen semantics.
- `ui/app.py` derives `_CANDLE_INTERVAL_NS`, warmup cadence (`_WARMUP_DT_NS = bar_ns`), and
  `_WARMUP_BARS` from the active spec instead of module-level second constants.
- `CandleAggregator(interval_ns=spec.bar_ns)` for both price and crypto candles.
- Chart x-axis / bar count (`CandleAggregator maxlen`) stays bar-count based, so 15m bars display
  the same way 1s bars did — only the span each bar covers changes.

### 4.2 Timeframe selector + hot-apply

- New Settings row: **Timeframe** `Select` over `TIMEFRAMES` keys, default = current
  `cfg.timeframe`.
- On save, if timeframe changed:
  1. Build new `EngineConfig` from the selected `TimeframeSpec`, merged with any user-edited
     engine thresholds (spike/snapdrop).
  2. Rebuild `AppConfig` via `msgspec.structs.replace`.
  3. Hot-apply: `app.engine.cfg = new_engine_cfg`; re-create `CandleAggregator`s with the new
     `bar_ns` (existing bars cleared — mixing bar sizes is invalid); reset momentum/window state
     as needed; re-run warmup (`_warmup_strategies` / `_warmup_crypto`).
- Feed cadence (`equity_tps`) is **independent** of timeframe and unchanged; ticks simply
  aggregate into larger buckets.

### 4.3 Remove the trade-frequency (Risk Management Mode) setting

- Rebuilt `SettingsScreen` does **not** render the Risk Management Mode `Select`, its label, or
  read `#set-risk`.
- Delete `SettingsConfirmScreen` and the risk-change confirmation branch (it only existed to
  confirm risk-mode changes from the UI).
- `AppConfig.risk_profile` field **remains** with its default (`"medium"`) so nothing downstream
  breaks; it is simply no longer user-editable from the terminal settings.
- `src/entropy/bot/*` (including `BotConfig.risk_profile`, `risk/profiles.py`, `risk/manager.py`)
  and `_on_strategy` auto-trading are **unchanged**.

### 4.4 Settings screen rebuild

New `SettingsScreen` composed of labelled **sections** (using `Vertical` groups with section
headers), scaled for terminal readability:

1. **Appearance** — Visual Theme (Select, 7 themes), Chart Style (candlestick/line), Show Volume
   (Switch).
2. **Timeframe** — Timeframe (Select over `TIMEFRAMES`).
3. **Data Feeds** — Enable Equities (Switch), Enable Live Crypto (Switch), Equity Sim TPS
   (Input, int), Equity Strategy Symbol (Input), Crypto Strategy Symbol (Input).
4. **Scanner / Engine** — Spike % Threshold (Input, float), Snapdrop % Threshold (Input, float).

- Buttons: **Save Changes** / **Cancel**.
- **Hot-apply** semantics preserved: theme, chart type, volume visibility, TPS, engine config,
  strategy symbols (with re-warmup on change), plus the new timeframe path (4.2).
- **Input validation:** non-numeric TPS/threshold → `ErrorScreen("Invalid input: …")`, panel
  stays open, no crash, save re-enabled.
- **Layout:** no overlap/breakage on open, on theme switch, or across all 7 themes; sections
  scroll if content exceeds height.
- CSS: update `entropy.tcss` for the new section structure; remove now-dead `#set-risk`/confirm
  styles; add section-header styling.

### 4.5 Error handling

- Timeframe/engine reconfiguration is wrapped so a bad value cannot leave the app in a
  half-applied state (validate → build new config → apply atomically; on failure show
  `ErrorScreen`, keep old config).
- Warmup remains best-effort (network hiccups surface as console INFO / error text), unchanged.

## 5. Testing strategy

- **New:** `tests/engine/test_timeframe.py` — `TIMEFRAMES` registry integrity (monotone spans,
  15m default, bar_ns correctness), `EngineConfig.from_timeframe` mapping, session window
  presence.
- **New:** settings-selector tests — timeframe Select present with correct default; changing it
  hot-applies (candle interval changes, aggregators reset, warmup re-runs); Risk Management Mode
  row is **absent**; `SettingsConfirmScreen` no longer referenced.
- **Update (timeframe-coupled):** `test_candles`, `test_windows_extreme`, `test_momentum_horizon`,
  `test_rate`, `test_settings_integration`, `test_settings_adversarial`,
  `test_settings_challenger_stress`, `test_modals`, `test_app_boots`, and any test asserting
  second-scale window keys.
- **Regression:** full `pytest` suite green; `ruff` + `mypy` clean.
- **Bot suite untouched:** `tests/bot/*` must remain green with no edits caused by this project
  (proves the bot/auto-trade were not disturbed).

## 6. Subagent-driven bug hunt (post-implementation)

Parallel review agents, each self-contained, then a referee gate before any commit of findings:

- **Engine agent** — timeframe bucketing (`ts_ns // bar_ns`), off-by-one on window eviction,
  session vs rolling windows, monotone-deque correctness at 15m spans.
- **UI/Settings agent** — rebuilt panel: section layout, theme switching, hot-apply of every
  control, timeframe selector reconfigure, validation paths, no `#set-risk`/confirm remnants.
- **Feeds/Warmup agent** — 15m warmup sufficiency, aggregator reset on timeframe change, feed
  cadence independence.
- **Regression/Removal agent** — full suite + confirms bot/auto-trade untouched; hunts for dead
  references to Risk Management Mode / `SettingsConfirmScreen`.

**Referee-gate:** commit only when every area reports READY. No fabricated metrics; caveats
allowed. No `Co-Authored-By: Claude` trailer.

## 7. Out of scope

- Any change to the bot subsystem behavior, auto-trade logic, risk profiles, or ledger.
- Feed protocol / transport changes.
- New themes or chart types.
- Persisting settings to disk (settings remain session-scoped, as today).

## 8. Execution flow

`brainstorming` (this doc) → `writing-plans` (implementation plan) →
`subagent-driven-development` (implementation + bug hunt).
