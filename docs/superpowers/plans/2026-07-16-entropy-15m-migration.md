# Entropy 15m Timeframe Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Entropy TUI terminal from a second-scale timeframe (1s candles, 30s/1m/5m/20m scanner windows) to a selectable 15-minute-centric timeframe, rebuild the Settings screen from scratch (adding a timeframe selector, removing the Risk Management Mode "trade-frequency" control), and verify with a subagent-driven bug hunt.

**Architecture:** Introduce a single source of truth (`engine/timeframe.py`) mapping a timeframe name → bar interval, 3 ordered rolling scanner windows (+ cumulative session), momentum/breadth/cooldown spans, and warmup. Window identity becomes **positional** (W0/W1/W2/SESSION); display labels are timeframe-derived so the runtime selector can change them. `EngineConfig` derives from the active `TimeframeSpec`; `AppConfig` gains a `timeframe` field defaulting to `15m`. The bot subsystem and auto-trade logic are untouched.

**Tech Stack:** Python 3.13, Textual, msgspec, pytest, ruff, mypy, uv.

---

## Baseline & Conventions

- Work on branch `feat/entropy-15m-migration` (already created).
- Run everything through uv: tests `uv run pytest …`, lint `uv run ruff check src tests`, types `uv run mypy src`.
- **Commit gate (Opsera):** before each `git commit`, run `touch /tmp/.opsera-pre-commit-scan-passed` as a **separate** Bash call, then `git commit` as its own Bash call. Never combine `git add`+`git commit` in one call. No `Co-Authored-By: Claude` trailer.
- TDD: write failing test → confirm failure → implement → confirm pass → commit.
- **Do NOT modify** `src/entropy/bot/**` or `tests/bot/**`. The bot keeps its own `risk_profile`. If a change appears to require touching bot code or bot tests, stop and flag it.

## Locked design decisions

- **3 rolling windows + cumulative session**, uniform across all timeframes (down from 4).
- **Positional window identity:** `WindowName` = `W0/W1/W2/SESSION`. Display labels come from the active `TimeframeSpec`.
- **Default timeframe:** `15m` → rolling windows `15m / 1h / 4h` + `session`.
- **TIMEFRAMES registry** (defaults; structure fixed, numeric values may be tuned during the bug hunt and this table updated to match):

| name | bar | rolling windows | momentum_horizon_s | breadth_window_s | momentum_cooldown_s | warmup_bars |
|------|-----|-----------------|--------------------|------------------|---------------------|-------------|
| 1m   | 1m  | 1m / 5m / 15m   | 30   | 60    | 30    | 24 |
| 5m   | 5m  | 5m / 15m / 1h   | 150  | 300   | 150   | 24 |
| **15m** | **15m** | **15m / 1h / 4h** | **450** | **900** | **450** | **24** |
| 1h   | 1h  | 1h / 4h / 1d    | 1800 | 3600  | 1800  | 24 |
| 4h   | 4h  | 4h / 12h / 1d   | 7200 | 14400 | 7200  | 24 |

- Time unit constants (ns): `1m=60_000_000_000`, `1h=3_600_000_000_000`, `4h=14_400_000_000_000`, `12h=43_200_000_000_000`, `1d=86_400_000_000_000`, `15m=900_000_000_000`, `5m=300_000_000_000`.

---

## Task 1: Timeframe registry module

**Files:**
- Create: `src/entropy/engine/timeframe.py`
- Test: `tests/engine/test_timeframe.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_timeframe.py
import pytest

from entropy.engine.timeframe import (
    DEFAULT_TIMEFRAME,
    TIMEFRAMES,
    TimeframeSpec,
    get_timeframe,
)

_S = 1_000_000_000


def test_default_is_15m():
    assert DEFAULT_TIMEFRAME == "15m"
    assert DEFAULT_TIMEFRAME in TIMEFRAMES


def test_15m_spec_values():
    spec = get_timeframe("15m")
    assert isinstance(spec, TimeframeSpec)
    assert spec.name == "15m"
    assert spec.bar_ns == 900 * _S
    assert spec.window_labels == ("15m", "1h", "4h")
    assert spec.windows_ns == (900 * _S, 3600 * _S, 4 * 3600 * _S)
    assert spec.momentum_horizon_s == 450.0
    assert spec.breadth_window_s == 900
    assert spec.momentum_cooldown_ns == 450 * _S
    assert spec.warmup_bars == 24


def test_every_spec_has_three_ordered_rolling_windows():
    for name, spec in TIMEFRAMES.items():
        assert len(spec.window_labels) == 3, name
        assert len(spec.windows_ns) == 3, name
        # strictly increasing spans
        assert spec.windows_ns[0] < spec.windows_ns[1] < spec.windows_ns[2], name


def test_get_timeframe_unknown_raises():
    with pytest.raises(KeyError):
        get_timeframe("7m")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_timeframe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.engine.timeframe'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/entropy/engine/timeframe.py
from __future__ import annotations

import msgspec

_S = 1_000_000_000
_MIN = 60 * _S
_HOUR = 3600 * _S
_DAY = 24 * _HOUR


class TimeframeSpec(msgspec.Struct, frozen=True):
    """All timeframe-derived engine parameters, keyed by a timeframe name."""

    name: str
    bar_ns: int
    window_labels: tuple[str, str, str]
    windows_ns: tuple[int, int, int]
    momentum_horizon_s: float
    breadth_window_s: int
    momentum_cooldown_ns: int
    warmup_bars: int = 24


def _spec(
    name: str,
    bar_ns: int,
    labels: tuple[str, str, str],
    spans: tuple[int, int, int],
    horizon_s: float,
    breadth_s: int,
    cooldown_s: float,
) -> TimeframeSpec:
    return TimeframeSpec(
        name=name,
        bar_ns=bar_ns,
        window_labels=labels,
        windows_ns=spans,
        momentum_horizon_s=horizon_s,
        breadth_window_s=breadth_s,
        momentum_cooldown_ns=int(cooldown_s * _S),
    )


TIMEFRAMES: dict[str, TimeframeSpec] = {
    "1m": _spec("1m", _MIN, ("1m", "5m", "15m"), (_MIN, 5 * _MIN, 15 * _MIN), 30.0, 60, 30.0),
    "5m": _spec("5m", 5 * _MIN, ("5m", "15m", "1h"), (5 * _MIN, 15 * _MIN, _HOUR), 150.0, 300, 150.0),
    "15m": _spec("15m", 15 * _MIN, ("15m", "1h", "4h"), (15 * _MIN, _HOUR, 4 * _HOUR), 450.0, 900, 450.0),
    "1h": _spec("1h", _HOUR, ("1h", "4h", "1d"), (_HOUR, 4 * _HOUR, _DAY), 1800.0, 3600, 1800.0),
    "4h": _spec("4h", 4 * _HOUR, ("4h", "12h", "1d"), (4 * _HOUR, 12 * _HOUR, _DAY), 7200.0, 14400, 7200.0),
}

DEFAULT_TIMEFRAME = "15m"


def get_timeframe(name: str) -> TimeframeSpec:
    if name not in TIMEFRAMES:
        raise KeyError(f"Unknown timeframe {name!r}; choose from {sorted(TIMEFRAMES)}.")
    return TIMEFRAMES[name]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_timeframe.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

Separate calls:
```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/engine/timeframe.py tests/engine/test_timeframe.py && git commit -m "feat(engine): timeframe registry (15m default, positional windows)"
```

---

## Task 2: Positional WindowName enum

**Files:**
- Modify: `src/entropy/engine/events.py:8-12`
- Test: `tests/engine/test_events_windowname.py` (Create)

Currently `WindowName` is `S30/M1/M5/M20/SESSION` with string values `"30s"/"1m"/…`. Make it positional so display labels can vary by timeframe.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_events_windowname.py
from entropy.engine.events import WindowName


def test_positional_members():
    assert WindowName.W0.value == "w0"
    assert WindowName.W1.value == "w1"
    assert WindowName.W2.value == "w2"
    assert WindowName.SESSION.value == "session"
    # exactly these four members
    assert {w.name for w in WindowName} == {"W0", "W1", "W2", "SESSION"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_events_windowname.py -v`
Expected: FAIL — `AttributeError: W0` (old members are S30/M1/M5/M20).

- [ ] **Step 3: Edit `events.py`**

Replace the enum body (lines 8-12) so it reads exactly:

```python
class WindowName(enum.StrEnum):
    W0 = "w0"
    W1 = "w1"
    W2 = "w2"
    SESSION = "session"
```

(Keep the rest of `events.py` unchanged; `SESSION` already existed and is referenced by `NewHigh`/`NewLow` defaults.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_events_windowname.py -v`
Expected: PASS. (Engine/others still reference old names and will be fixed next task — do NOT run the full suite yet.)

- [ ] **Step 5: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/engine/events.py tests/engine/test_events_windowname.py && git commit -m "feat(engine): make WindowName positional (W0/W1/W2/SESSION)"
```

---

## Task 3: EngineConfig derives from TimeframeSpec

**Files:**
- Modify: `src/entropy/config.py` (whole file)
- Test: `tests/engine/test_engine_config.py` (Create)

`EngineConfig` must (a) key `windows_ns` by positional enum values `"w0"/"w1"/"w2"`, (b) carry `window_labels`, and (c) provide a `from_timeframe` factory. Default = 15m.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_engine_config.py
from entropy.config import EngineConfig
from entropy.engine.timeframe import get_timeframe

_S = 1_000_000_000


def test_default_engine_config_is_15m():
    cfg = EngineConfig()
    assert cfg.windows_ns == {"w0": 900 * _S, "w1": 3600 * _S, "w2": 4 * 3600 * _S}
    assert cfg.window_labels == ("15m", "1h", "4h")
    assert cfg.momentum_horizon_s == 450.0
    assert cfg.breadth_window_s == 900
    assert cfg.momentum_cooldown_ns == 450 * _S


def test_from_timeframe_1h():
    cfg = EngineConfig.from_timeframe(get_timeframe("1h"))
    assert cfg.window_labels == ("1h", "4h", "1d")
    assert cfg.windows_ns == {"w0": 3600 * _S, "w1": 4 * 3600 * _S, "w2": 86400 * _S}
    assert cfg.breadth_window_s == 3600
    # non-timeframe fields keep their defaults
    assert cfg.spike_pct == 0.40
    assert cfg.leaderboard_k == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_engine_config.py -v`
Expected: FAIL — `windows_ns` keys are old (`"30s"…`) / no `from_timeframe`.

- [ ] **Step 3: Rewrite `config.py`**

```python
from __future__ import annotations

import msgspec

from entropy.engine.timeframe import TimeframeSpec, get_timeframe

_DEFAULT_SPEC = get_timeframe("15m")


def _default_windows() -> dict[str, int]:
    return {
        "w0": _DEFAULT_SPEC.windows_ns[0],
        "w1": _DEFAULT_SPEC.windows_ns[1],
        "w2": _DEFAULT_SPEC.windows_ns[2],
    }


class EngineConfig(msgspec.Struct, frozen=True):
    windows_ns: dict[str, int] = msgspec.field(default_factory=_default_windows)
    window_labels: tuple[str, str, str] = _DEFAULT_SPEC.window_labels
    momentum_horizon_s: float = _DEFAULT_SPEC.momentum_horizon_s
    spike_pct: float = 0.40
    snapdrop_pct: float = 0.40
    upmove_pct: float = 0.15
    downmove_pct: float = 0.15
    momentum_cooldown_ns: int = _DEFAULT_SPEC.momentum_cooldown_ns
    new_extreme_strict: bool = True
    breadth_window_s: int = _DEFAULT_SPEC.breadth_window_s
    leaderboard_k: int = 20
    accel_eps: float = 0.10

    @classmethod
    def from_timeframe(cls, spec: TimeframeSpec) -> "EngineConfig":
        return cls(
            windows_ns={"w0": spec.windows_ns[0], "w1": spec.windows_ns[1], "w2": spec.windows_ns[2]},
            window_labels=spec.window_labels,
            momentum_horizon_s=spec.momentum_horizon_s,
            breadth_window_s=spec.breadth_window_s,
            momentum_cooldown_ns=spec.momentum_cooldown_ns,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_engine_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/config.py tests/engine/test_engine_config.py && git commit -m "feat(engine): EngineConfig.from_timeframe + positional window keys"
```

---

## Task 4: Engine uses 3 positional rolling windows

**Files:**
- Modify: `src/entropy/engine/engine.py` (lines 13, 22-23, 32-33, 97-120, 170-205, 231-232)
- Test: `tests/engine/test_engine_windows_positional.py` (Create)

Reduce the rolling-window structure from 4 to 3 and reference positional enum members. The per-window loops already use `zip(_WIN_ORDER, …)` / `enumerate(_WIN_ORDER)` so they adapt automatically; only the literal `range(4)` and `_WIN_ORDER` need editing. Snapshot count dicts must be keyed by **display label** (`cfg.window_labels`) in window order.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_engine_windows_positional.py
from entropy.engine.engine import Engine, _WIN_ORDER
from entropy.engine.events import WindowName

_S = 1_000_000_000


def test_win_order_is_three_positional():
    assert _WIN_ORDER == (WindowName.W0, WindowName.W1, WindowName.W2)


def test_snapshot_counts_keyed_by_display_label():
    eng = Engine()
    # feed a rising then falling sequence for one symbol
    ts = 0
    for px in (100.0, 101.0, 102.0, 101.0, 99.0):
        ts += 1 * _S
        eng.on_trade("AAA", px, 1.0, "buy", ts)
    snap = eng.snapshot()
    # nh/nl count dicts use the 15m display labels, in order
    assert list(snap.breadth.nh_counts.keys()) == ["15m", "1h", "4h"]
    assert list(snap.breadth.nl_counts.keys()) == ["15m", "1h", "4h"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_engine_windows_positional.py -v`
Expected: FAIL — `_WIN_ORDER` still 4 old members / count keys wrong.

- [ ] **Step 3: Edit `engine.py`**

1. Line 13:
```python
_WIN_ORDER = (WindowName.W0, WindowName.W1, WindowName.W2)
```
2. Lines 32-33 (`_Tape.__init__`): change both `range(4)` → `range(3)`.
3. Lines 231-232 (snapshot reset of `nh_by_win`/`nl_by_win`): change both `range(4)` → `range(3)`.
4. In `snapshot()` where `nh_counts`/`nl_counts` dicts are built (around lines 170-205): key them by `self.cfg.window_labels[i]` for `i, w in enumerate(_WIN_ORDER)` instead of by `w.value`. Likewise, `TickerGroup.window` must be set to `self.cfg.window_labels[i]` (the display label), not `w.value`. Read the current lines with the Read tool first; replace each `w.value` used as an outward-facing key/label in snapshot construction with `self.cfg.window_labels[i]`. Keep internal indexing by position.

> Note: `NewHigh`/`NewLow`/`Spike` event `window` fields that use `WindowName.SESSION` stay as-is. Per-rolling-window events (if any set `window=w`) keep the positional enum internally; only the **snapshot count dicts and ticker groups** switch to display labels.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_engine_windows_positional.py tests/engine/test_engine.py -v`
Expected: `test_engine_windows_positional` PASS. Some existing `test_engine.py` assertions referencing old labels may fail — those are fixed in Task 10. If `test_engine.py` failures are only about window labels/counts, that is expected; note them for Task 10.

- [ ] **Step 5: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/engine/engine.py tests/engine/test_engine_windows_positional.py && git commit -m "feat(engine): 3 positional rolling windows, label-keyed snapshot"
```

---

## Task 5: AppConfig.timeframe + UI derives interval/warmup from spec

**Files:**
- Modify: `src/entropy/app.py` (AppConfig)
- Modify: `src/entropy/ui/app.py` (lines 36-39 constants + `__init__` + `_synth_spy_bars`)
- Test: `tests/ui/test_timeframe_wiring.py` (Create)

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_timeframe_wiring.py
import pytest

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.engine.timeframe import get_timeframe


def test_appconfig_default_timeframe():
    assert AppConfig().timeframe == "15m"


@pytest.mark.asyncio
async def test_candle_interval_matches_timeframe():
    app = EntropyApp(AppConfig(enable_crypto=False, timeframe="15m"))
    async with app.run_test(size=(120, 60)):
        spec = get_timeframe("15m")
        assert app._price_candles.interval_ns == spec.bar_ns
        assert app._crypto_candles.interval_ns == spec.bar_ns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_timeframe_wiring.py -v`
Expected: FAIL — `AppConfig` has no `timeframe`; candles use 1s interval.

- [ ] **Step 3: Implement**

In `src/entropy/app.py`, add to `AppConfig` (before `engine`):
```python
    timeframe: str = "15m"
```
And make the engine default match the timeframe by leaving `engine` default as `EngineConfig()` (already 15m). (If `AppConfig` is constructed with a non-default `timeframe`, `ui/app.py` will build the matching engine — next.)

In `src/entropy/ui/app.py`:
1. Replace the module constants block (lines ~36-39) with spec-derived values built in `__init__`. Remove `_CANDLE_INTERVAL_NS`, `_WARMUP_BARS`, `_WARMUP_DT_NS` module constants; instead, in `EntropyApp.__init__`, after `self.cfg = config or AppConfig()`:
```python
        from entropy.engine.timeframe import get_timeframe
        from entropy.config import EngineConfig
        self._tf = get_timeframe(self.cfg.timeframe)
        # keep engine config consistent with the selected timeframe
        self.engine = Engine(EngineConfig.from_timeframe(self._tf))
        self._candle_interval_ns = self._tf.bar_ns
        self._warmup_bars = self._tf.warmup_bars
        self._warmup_dt_ns = self._tf.bar_ns
```
   Remove the now-duplicate `self.engine = Engine(self.cfg.engine)` line (the spec-derived engine replaces it).
2. Update both `CandleAggregator(_CANDLE_INTERVAL_NS)` constructions to `CandleAggregator(self._candle_interval_ns)`.
3. In `_synth_spy_bars`, replace `_WARMUP_BARS` → `self._warmup_bars` and `_WARMUP_DT_NS` → `self._warmup_dt_ns`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_timeframe_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/app.py src/entropy/ui/app.py tests/ui/test_timeframe_wiring.py && git commit -m "feat(ui): derive candle interval + warmup from active timeframe"
```

---

## Task 6: Scanner widgets use positional labels + styles

**Files:**
- Modify: `src/entropy/ui/widgets/highlow_gauges.py:7` (and its render)
- Modify: `src/entropy/ui/widgets/ticker_strip.py:25` (style-by-position)
- Modify: `src/entropy/ui/widgets/status_bar.py:19,24` (neutral "prev" label)
- Test: `tests/ui/test_scanner_labels.py` (Create)

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_scanner_labels.py
import pytest

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.highlow_gauges import HighLowGauges


@pytest.mark.asyncio
async def test_gauges_use_timeframe_labels():
    app = EntropyApp(AppConfig(enable_crypto=False, timeframe="15m"))
    async with app.run_test(size=(120, 60)):
        gauges = app.query_one("#hist", HighLowGauges)
        # gauges pull labels from the app's active engine config
        assert gauges.window_labels == ("15m", "1h", "4h")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_scanner_labels.py -v`
Expected: FAIL — `HighLowGauges` has fixed `_WINDOWS` and no `window_labels`.

- [ ] **Step 3: Implement**

Read each widget first. Then:

- `highlow_gauges.py`: remove module-level `_WINDOWS = ("30s","1m","5m","20m")`. Add a reactive/attribute `window_labels: tuple[str, ...] = ("15m", "1h", "4h")` and iterate it (with `nh_counts`/`nl_counts` dicts now keyed by these labels). In `ui/app.py` `on_mount` / `sample_snapshot`, set `self.query_default("#hist", HighLowGauges).window_labels = self.cfg.engine.window_labels` once (on mount) and after timeframe changes.
- `ticker_strip.py`: replace the label-keyed `win_style = {"30s": success, …}` with position-based styling: build the style list in order `(success, accent, primary)` and zip with the incoming ordered groups, so any labels render correctly.
- `status_bar.py`: rename the `prev30s` parameter/label to a neutral `prev` in `format_telemetry` and its call site in `ui/app.py:133-134` (change kwarg `prev30s=` → `prev=`), and the displayed text from `prev30s: {…}` to `prev: {…}/s`. (The value still comes from `snap.breadth.prev30s_rate`; only the outward label changes.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_scanner_labels.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/ui/widgets/highlow_gauges.py src/entropy/ui/widgets/ticker_strip.py src/entropy/ui/widgets/status_bar.py src/entropy/ui/app.py tests/ui/test_scanner_labels.py && git commit -m "feat(ui): scanner widgets render timeframe labels, style by position"
```

---

## Task 7: Rebuild SettingsScreen (sections, timeframe selector, no Risk Mode)

**Files:**
- Modify: `src/entropy/ui/widgets/modals.py` (rewrite `SettingsScreen`, delete `SettingsConfirmScreen`, update help text)
- Modify: `src/entropy/ui/app.py` (remove `SettingsConfirmScreen`-related imports if any; keep `SettingsScreen`)
- Test: `tests/ui/test_settings_rebuild.py` (Create)

The rebuilt screen has 4 sections (Appearance, Timeframe, Data Feeds, Scanner/Engine). It **omits** the Risk Management Mode control. `SettingsConfirmScreen` is deleted (it existed only to confirm risk-mode changes). `AppConfig.risk_profile` stays in the struct with its default.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_settings_rebuild.py
import pytest
from textual.widgets import Select

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets import modals
from entropy.ui.widgets.modals import SettingsScreen


def test_confirm_screen_removed():
    assert not hasattr(modals, "SettingsConfirmScreen")


@pytest.mark.asyncio
async def test_settings_has_timeframe_and_no_risk():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        # timeframe selector present with current default
        tf = screen.query_one("#set-timeframe", Select)
        assert tf.value == "15m"
        # Risk Management Mode control is gone
        from textual.css.query import NoMatches
        with pytest.raises(NoMatches):
            screen.query_one("#set-risk", Select)


@pytest.mark.asyncio
async def test_timeframe_change_hot_applies(monkeypatch):
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.press("s")
        screen = app.screen
        screen.query_one("#set-timeframe", Select).value = "1h"
        await pilot.click("#btn-save")
        await pilot.pause()
        assert app.cfg.timeframe == "1h"
        assert app._candle_interval_ns == 3_600_000_000_000
        assert app.cfg.engine.window_labels == ("1h", "4h", "1d")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_settings_rebuild.py -v`
Expected: FAIL — `SettingsConfirmScreen` still exists / no `#set-timeframe`.

- [ ] **Step 3: Implement (rewrite `SettingsScreen`)**

Replace `SettingsScreen` and remove `SettingsConfirmScreen` entirely. New `compose` builds sections; keep control ids for reused fields (`#set-theme`, `#set-chart`, `#set-volume`, `#set-equities`, `#set-crypto`, `#set-tps`, `#set-strat-sym`, `#set-crypto-sym`, `#set-spike`, `#set-snapdrop`), add `#set-timeframe`, drop `#set-risk`.

```python
class SettingsScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saving = False

    def compose(self) -> ComposeResult:
        from entropy.engine.timeframe import TIMEFRAMES
        cfg = self.app.cfg  # type: ignore
        theme_options = [
            ("Entropy (Default)", "entropy"), ("Dracula", "dracula"),
            ("Cyberpunk", "cyberpunk"), ("Nord", "nord"), ("Forest", "forest"),
            ("Monochrome", "monochrome"), ("Sweet", "sweet"),
        ]
        chart_options = [("Candlestick", "candlestick"), ("Line Plot", "line")]
        tf_options = [(name, name) for name in TIMEFRAMES]

        with Vertical(id="settings-container"):
            yield Static("Settings", id="settings-title")
            with Vertical(id="settings-form"):
                yield Static("Appearance", classes="settings-section")
                with Horizontal(classes="settings-row"):
                    yield Label("Visual Theme:")
                    yield Select(options=theme_options, value=cfg.theme, id="set-theme", allow_blank=False)
                with Horizontal(classes="settings-row"):
                    yield Label("Chart Style:")
                    yield Select(options=chart_options, value=cfg.chart_type, id="set-chart", allow_blank=False)
                with Horizontal(classes="settings-row"):
                    yield Label("Show Volume Charts:")
                    yield Switch(value=cfg.show_volume, id="set-volume")

                yield Static("Timeframe", classes="settings-section")
                with Horizontal(classes="settings-row"):
                    yield Label("Timeframe:")
                    yield Select(options=tf_options, value=cfg.timeframe, id="set-timeframe", allow_blank=False)

                yield Static("Data Feeds", classes="settings-section")
                with Horizontal(classes="settings-row"):
                    yield Label("Enable Equities Feed:")
                    yield Switch(value=cfg.enable_equities, id="set-equities")
                with Horizontal(classes="settings-row"):
                    yield Label("Enable Live Crypto Feed:")
                    yield Switch(value=cfg.enable_crypto, id="set-crypto")
                with Horizontal(classes="settings-row"):
                    yield Label("Equity Sim Ticks/Sec (TPS):")
                    yield Input(value=str(cfg.equity_tps), id="set-tps")
                with Horizontal(classes="settings-row"):
                    yield Label("Equity Strategy Symbol:")
                    yield Input(value=cfg.strategy_symbol, id="set-strat-sym")
                with Horizontal(classes="settings-row"):
                    yield Label("Crypto Strategy Symbol:")
                    yield Input(value=cfg.crypto_strategy_symbol, id="set-crypto-sym")

                yield Static("Scanner / Engine", classes="settings-section")
                with Horizontal(classes="settings-row"):
                    yield Label("Engine Spike % Threshold:")
                    yield Input(value=str(cfg.engine.spike_pct), id="set-spike")
                with Horizontal(classes="settings-row"):
                    yield Label("Engine Snapdrop % Threshold:")
                    yield Input(value=str(cfg.engine.snapdrop_pct), id="set-snapdrop")

            with Horizontal(id="settings-buttons"):
                yield Button("Save Changes", variant="success", id="btn-save")
                yield Button("Cancel", variant="error", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss()
            return
        if event.button.id != "btn-save":
            return
        if self._saving:
            return
        self._saving = True
        try:
            theme_val = str(self.query_one("#set-theme", Select).value)
            chart_val = str(self.query_one("#set-chart", Select).value)
            vol_val = self.query_one("#set-volume", Switch).value
            tf_val = str(self.query_one("#set-timeframe", Select).value)
            equities_val = self.query_one("#set-equities", Switch).value
            crypto_val = self.query_one("#set-crypto", Switch).value
            tps_val = int(self.query_one("#set-tps", Input).value)
            strat_sym_val = self.query_one("#set-strat-sym", Input).value.upper()
            crypto_sym_val = self.query_one("#set-crypto-sym", Input).value
            spike_val = float(self.query_one("#set-spike", Input).value)
            snap_val = float(self.query_one("#set-snapdrop", Input).value)
        except ValueError as e:
            self._saving = False
            self.app.push_screen(ErrorScreen(f"Invalid input: {e}", id="errors"))
            return

        self.app._apply_settings(  # type: ignore
            theme=theme_val, chart_type=chart_val, show_volume=vol_val,
            timeframe=tf_val, enable_equities=equities_val, enable_crypto=crypto_val,
            equity_tps=tps_val, strategy_symbol=strat_sym_val,
            crypto_strategy_symbol=crypto_sym_val, spike_pct=spike_val, snapdrop_pct=snap_val,
        )
        self.dismiss()
```

Also update `_HELP` text: change `over 30s/1m/5m/20m/session` to `over 3 rolling windows + session (timeframe-selectable)`.

Delete the `SettingsConfirmScreen` class. The hot-apply logic (including the timeframe path) moves into `EntropyApp._apply_settings` in Task 9.

- [ ] **Step 4: Run test to verify it fails on `_apply_settings`**

Run: `uv run pytest tests/ui/test_settings_rebuild.py::test_settings_has_timeframe_and_no_risk tests/ui/test_settings_rebuild.py::test_confirm_screen_removed -v`
Expected: These two PASS. `test_timeframe_change_hot_applies` will FAIL until Task 9 (`_apply_settings`) exists — that's expected; leave it.

- [ ] **Step 5: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/ui/widgets/modals.py tests/ui/test_settings_rebuild.py && git commit -m "feat(ui): rebuild SettingsScreen (sections + timeframe), drop Risk Mode + confirm screen"
```

---

## Task 8: Hot-apply settings incl. timeframe reconfigure

**Files:**
- Modify: `src/entropy/ui/app.py` (add `_apply_settings`)
- Test: reuses `tests/ui/test_settings_rebuild.py::test_timeframe_change_hot_applies`

- [ ] **Step 1: Confirm the failing test**

Run: `uv run pytest tests/ui/test_settings_rebuild.py::test_timeframe_change_hot_applies -v`
Expected: FAIL — `EntropyApp` has no `_apply_settings`.

- [ ] **Step 2: Implement `_apply_settings`**

Add to `EntropyApp` (uses helpers already imported; import `msgspec`, `get_timeframe`, `EngineConfig`, `Strategy`, `StrategyConfig`, `PriceChart`, `VolumeChart` at top or locally):

```python
    def _apply_settings(
        self, *, theme: str, chart_type: str, show_volume: bool, timeframe: str,
        enable_equities: bool, enable_crypto: bool, equity_tps: int,
        strategy_symbol: str, crypto_strategy_symbol: str,
        spike_pct: float, snapdrop_pct: float,
    ) -> None:
        from entropy.config import EngineConfig
        from entropy.engine.timeframe import get_timeframe
        from entropy.strategy.engine import Strategy, StrategyConfig
        from entropy.engine.candles import CandleAggregator

        tf_changed = timeframe != self.cfg.timeframe
        spec = get_timeframe(timeframe)
        new_engine_cfg = msgspec.structs.replace(
            EngineConfig.from_timeframe(spec), spike_pct=spike_pct, snapdrop_pct=snapdrop_pct,
        )
        self.cfg = msgspec.structs.replace(
            self.cfg, theme=theme, chart_type=chart_type, show_volume=show_volume,
            timeframe=timeframe, enable_equities=enable_equities, enable_crypto=enable_crypto,
            equity_tps=equity_tps, strategy_symbol=strategy_symbol,
            crypto_strategy_symbol=crypto_strategy_symbol, engine=new_engine_cfg,
        )
        self.theme = theme
        self.query_default("#price", PriceChart).chart_type = chart_type
        self.query_default("#price2", PriceChart).chart_type = chart_type
        self.query_default("#volume", VolumeChart).display = show_volume
        self.query_default("#volume2", VolumeChart).display = show_volume
        if self._equity is not None:
            self._equity.tps = equity_tps

        if tf_changed:
            self._tf = spec
            self.engine = Engine(new_engine_cfg)
            self._candle_interval_ns = spec.bar_ns
            self._warmup_bars = spec.warmup_bars
            self._warmup_dt_ns = spec.bar_ns
            self._price_candles = CandleAggregator(spec.bar_ns)
            self._crypto_candles = CandleAggregator(spec.bar_ns)
            self.query_default("#hist", HighLowGauges).window_labels = spec.window_labels
            self._warmup_strategies()
        else:
            self.engine.cfg = new_engine_cfg

        if self.strategy.cfg.symbol != strategy_symbol:
            self.strategy = Strategy(StrategyConfig(symbol=strategy_symbol))
            self._warmup_strategies()
        if self.crypto_strategy.cfg.symbol != crypto_strategy_symbol:
            self.crypto_strategy = Strategy(StrategyConfig(symbol=crypto_strategy_symbol, fee_bps=1.0))
            self._warmup_crypto()
```

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_settings_rebuild.py -v`
Expected: all 3 PASS.

- [ ] **Step 4: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/ui/app.py && git commit -m "feat(ui): _apply_settings hot-applies timeframe + reconfigures engine/aggregators"
```

---

## Task 9: Settings CSS — sections + remove confirm/risk styles

**Files:**
- Modify: `src/entropy/ui/entropy.tcss` (settings block lines ~21-115)
- Test: `tests/ui/test_app_boots.py` (existing; must still pass — CSS parse + boot)

- [ ] **Step 1: Edit CSS**

Read the current settings block. Add a `.settings-section` rule (section header: bold, accent color, top margin), keep `#settings-container/-title/-form/.settings-row/#settings-buttons`, and **delete** `#confirm-container`, `#confirm-message`, `#confirm-buttons`, `#confirm-buttons Button` rules (the confirm screen is gone). Ensure `#settings-form` allows vertical scrolling (`overflow-y: auto;`) so all sections fit.

Example section rule to add:
```css
.settings-section {
    text-style: bold;
    color: $accent;
    margin-top: 1;
    padding-top: 1;
}
```

- [ ] **Step 2: Run boot + settings tests**

Run: `uv run pytest tests/ui/test_app_boots.py tests/ui/test_settings_rebuild.py -v`
Expected: PASS (CSS parses, app boots, settings render).

- [ ] **Step 3: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add src/entropy/ui/entropy.tcss && git commit -m "style(ui): settings sections, remove dead confirm/risk CSS"
```

---

## Task 10: Update timeframe-coupled existing tests

**Files (Modify/rewrite as needed):**
- `tests/engine/test_windows_extreme.py`, `tests/engine/test_momentum_horizon.py`, `tests/engine/test_rate.py`, `tests/engine/test_candles.py`, `tests/engine/test_engine.py`, `tests/engine/test_breadth.py`
- `tests/ui/test_settings_integration.py`, `tests/ui/test_settings_adversarial.py`, `tests/ui/test_settings_challenger_stress.py`, `tests/ui/test_stress_save_clicks.py`, `tests/ui/test_modals.py`, `tests/ui/test_gauges.py`, `tests/ui/test_ticker_strip.py`, `tests/ui/test_status_bar.py`

- [ ] **Step 1: Run the full non-bot suite to enumerate failures**

Run: `uv run pytest tests/engine tests/ui tests/feeds tests/strategy tests/test_cli.py tests/test_wiring.py -v`
Expected: A set of failures concentrated in the files above (old window labels `"30s"…`, `#set-risk`, `SettingsConfirmScreen`, `prev30s`, 1s candle assumptions, 4-window counts).

- [ ] **Step 2: Fix each failing test to the new contract**

For each failure, update assertions to the new reality:
- Window labels → `("15m","1h","4h")` (or the timeframe under test).
- Count dicts keyed by display label, 3 entries.
- Remove references to `#set-risk`, `SettingsConfirmScreen`, risk-confirm flow. Settings tests that changed risk mode should instead exercise the timeframe selector or another field.
- `format_telemetry` kwarg `prev30s=` → `prev=`; displayed text `prev:`.
- Candle-interval assumptions: use `get_timeframe(tf).bar_ns` instead of `1_000_000_000`.
- `test_modals.py` open/close assertions stay valid; only update any risk/confirm references.

Do not weaken coverage — keep the behavioral intent, only update the expected values/ids. If a test's entire purpose was the removed Risk Mode control, replace it with an equivalent test for the timeframe selector.

- [ ] **Step 3: Run until green**

Run: `uv run pytest tests/engine tests/ui tests/feeds tests/strategy tests/test_cli.py tests/test_wiring.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add tests/engine tests/ui tests/feeds tests/strategy tests/test_cli.py tests/test_wiring.py && git commit -m "test: update timeframe-coupled tests to 15m/positional/settings-rebuild contract"
```

---

## Task 11: Full regression, lint, types, bot-untouched proof

**Files:** none (verification only)

- [ ] **Step 1: Full suite (incl. bot)**

Run: `uv run pytest -q`
Expected: PASS, no failures. If any `tests/bot/**` test fails, STOP — the bot was disturbed; revert the offending change.

- [ ] **Step 2: Confirm bot source untouched**

Run: `git diff --name-only main...HEAD -- src/entropy/bot`
Expected: **empty output** (no bot source files changed).

- [ ] **Step 3: Lint + types**

Run: `uv run ruff check src tests`
Run: `uv run mypy src`
Expected: both clean (fix any issues, re-run).

- [ ] **Step 4: Dead-reference sweep**

Run: `grep -rn "SettingsConfirmScreen\|set-risk\|Risk Management Mode\|prev30s\|\"30s\"\|\"20m\"\|_CANDLE_INTERVAL_NS\|range(4)" src/entropy`
Expected: no results in `src/entropy` (all migrated). Investigate/fix any hit.

- [ ] **Step 5: Commit any lint/type fixes**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add -A && git commit -m "chore: lint/type fixes + dead-reference cleanup for 15m migration"
```

---

## Task 12: Live-run smoke verification

**Files:** none (manual/driven verification)

- [ ] **Step 1: Boot headless run_test smoke**

Add/execute a smoke test that boots the app at 15m, opens Settings, switches timeframe to `1h`, saves, and asserts no exception + `app.cfg.timeframe == "1h"` and charts still render. (Reuse `test_timeframe_change_hot_applies` as the core; extend with a `pilot.pause()` and a `sample_snapshot()` call to ensure the redraw path is exercised post-change.)

Run: `uv run pytest tests/ui/test_settings_rebuild.py -v`
Expected: PASS.

- [ ] **Step 2: CLI launch check (non-interactive)**

Run: `uv run python -c "from entropy.app import AppConfig; from entropy.ui.app import EntropyApp; app=EntropyApp(AppConfig(enable_crypto=False)); print('tf', app.cfg.timeframe, 'bar_ns', app._candle_interval_ns)"`
Expected: `tf 15m bar_ns 900000000000`.

- [ ] **Step 3: Commit (if a smoke test file was added)**

```bash
touch /tmp/.opsera-pre-commit-scan-passed
```
```bash
git add tests/ui/test_settings_rebuild.py && git commit -m "test: live-run smoke for timeframe switch redraw path"
```

---

## Milestone: Subagent-driven bug hunt (after Task 12)

Dispatch parallel review agents (self-contained briefs), then a referee gate before committing any findings/fixes:

1. **Engine agent** — verify `ts_ns // bar_ns` bucketing, window eviction off-by-one at 15m spans, session vs rolling correctness, monotone-deque behavior; check `snapshot()` label keying and ticker order.
2. **UI/Settings agent** — rebuilt panel across all 7 themes: layout/no-overlap on open + theme switch, every control hot-applies, timeframe reconfigure + re-warmup, invalid-input → ErrorScreen (panel stays open), no `#set-risk`/confirm remnants.
3. **Feeds/Warmup agent** — 15m warmup sufficiency (`_synth_spy_bars` count/cadence), aggregator reset on timeframe change, feed cadence independence from timeframe.
4. **Regression/Removal agent** — full suite green; `git diff` proves `src/entropy/bot/**` and `tests/bot/**` untouched; dead-reference sweep clean.

**Referee gate:** every agent reports READY before final commit. No fabricated metrics; caveats allowed. No `Co-Authored-By: Claude` trailer.

---

## Self-Review (author checklist — completed)

- **Spec coverage:** Timeframe migration (Tasks 1-6, 8), selector (Task 7), remove trade-frequency/Risk Mode + confirm (Task 7, 9), settings rebuild (Task 7, 9), bug hunt (Milestone). Bot untouched (Task 11 proof). ✓
- **Placeholder scan:** No TBD/TODO; every code step has real code or exact edit instructions with ids/line ranges. Tasks that require reading current lines (engine snapshot, CSS, widgets) say so explicitly with the exact transformation. ✓
- **Type consistency:** `TimeframeSpec` fields, `EngineConfig.from_timeframe`, `WindowName.W0/W1/W2/SESSION`, `_apply_settings` signature, `window_labels` tuple used consistently across Tasks 1-8. `#set-timeframe` id consistent (Task 7 create, Task 8 read). ✓
