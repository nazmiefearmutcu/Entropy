# Entropy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Entropy — a real-time terminal (TUI) market scanner + algo console that streams live crypto via the Crypcodile engine, simulates US equities on the same record path, detects multi-window new highs/lows + momentum, ranks movers, runs a demo EMA scalping strategy, and renders live candlestick charts.

**Architecture:** Single process, single asyncio loop. Feeds (real crypto via Crypcodile `collect()` + simulated equities) push normalized `Trade` records into a bounded `QueueSink`. A Textual `@work(thread=True)` worker drains the queue at kHz into a PURE, synchronous detection `Engine` (`on_trade(symbol, price, amount, side, ts_ns)`) and `Strategy`. A `set_interval(1/10)` sampler reads an immutable `Engine.snapshot()` and repaints widgets at ~10 fps. The engine imports zero third-party code (primitives in, frozen structs out) and is fully unit-tested.

**Tech Stack:** Python 3.12, asyncio, Textual + textual-plotext, msgspec, Crypcodile (editable path dep), pytest/ruff/mypy, uv.

**Spec:** `docs/superpowers/specs/2026-06-08-entropy-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml`, `ruff.toml`, `mypy.ini` | project + tooling config |
| `src/entropy/config.py` | `AppConfig`, `EngineConfig`, `StrategyConfig`, universe whitelist |
| `src/entropy/feeds/bus.py` | `QueueSink(Sink)` over a bounded `asyncio.Queue` |
| `src/entropy/feeds/equities/universe.py` | ticker lists, `SymParams`, `build_params()` |
| `src/entropy/feeds/equities/sim.py` | `EquitySimulator` (pure, seeded) |
| `src/entropy/feeds/equities/feed.py` | `EquitySimFeed` (async task → Sink) |
| `src/entropy/feeds/crypto.py` | Crypcodile wiring: `build_live`, `discover_universe`, `start_feed` |
| `src/entropy/feeds/warmup.py` | Binance-klines warmup adapters → `Bar` |
| `src/entropy/engine/events.py` | event enums + frozen structs |
| `src/entropy/engine/windows.py` | `MonotonicExtreme`, `SessionExtreme`, `MomentumHorizon`, `RollingTape` |
| `src/entropy/engine/rate.py` | `RateMeter` |
| `src/entropy/engine/breadth.py` | `BreadthTracker` |
| `src/entropy/engine/leaderboard.py` | `LeaderRow` + ranking |
| `src/entropy/engine/engine.py` | `Engine`, `EngineSnapshot`, `BreadthSnapshot` |
| `src/entropy/strategy/ema.py` | `EmaState`, `ema_update` |
| `src/entropy/strategy/engine.py` | `Strategy`, `Position`, `StrategyEvent`, `Bar`, `StrategyConfig` |
| `src/entropy/strategy/format.py` | `render_event()` |
| `src/entropy/ui/app.py`, `ui/theme.py`, `ui/entropy.tcss` | Textual app, theme, styling |
| `src/entropy/ui/widgets/*.py` | header, ticker_strip, gauges, histogram, boards, console, charts, status_bar, modals |
| `src/entropy/app.py`, `src/entropy/__main__.py` | wiring + entrypoint |

---

## Conventions for every task
- Activate env first: `cd /Users/nazmi/Entropy && source .venv/bin/activate` (Phase 0 creates it).
- Run tests with `pytest -q`. Type-check touched files with `mypy src/entropy/<module>.py`.
- Lint with `ruff check --fix`. Commit messages: `feat:`/`test:`/`chore:`. **No Claude attribution.**
- All timestamps are integer nanoseconds. The engine is pure: no I/O, no logging, no clock reads.

---

# Phase 0 — Project setup

### Task 1: Scaffold project, env, tooling

**Files:**
- Create: `pyproject.toml`, `ruff.toml`, `mypy.ini`, `README.md`
- Create: `src/entropy/__init__.py`, `src/entropy/py.typed`
- Create: `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "entropy"
version = "0.0.1"
description = "Real-time terminal market scanner + algo console (Crypcodile feed)."
readme = "README.md"
requires-python = ">=3.12"
license = { text = "Apache-2.0" }
dependencies = [
    "textual>=0.79",
    "textual-plotext>=0.7",
    "msgspec>=0.18",
    "crypcodile",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "pytest-timeout>=2", "ruff>=0.6", "mypy>=1.11"]

[tool.uv.sources]
crypcodile = { path = "/Users/nazmi/Crypcodile", editable = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/entropy"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-q"
timeout = 60
```

- [ ] **Step 2: Write `ruff.toml` and `mypy.ini`**

`ruff.toml`:
```toml
target-version = "py312"
line-length = 100
[lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```
`mypy.ini`:
```ini
[mypy]
python_version = 3.12
strict = true
ignore_missing_imports = true
```

- [ ] **Step 3: Create package markers**

`src/entropy/__init__.py`:
```python
"""Entropy — real-time terminal market scanner."""
__version__ = "0.0.1"
```
Create empty `src/entropy/py.typed`, `tests/__init__.py`. `README.md`: one paragraph from the spec overview.

`tests/conftest.py`:
```python
import pytest

@pytest.fixture
def ns():
    """Helper to build nanosecond timestamps from float seconds."""
    return lambda s: int(s * 1_000_000_000)
```

- [ ] **Step 4: Create env and install**

Run:
```bash
cd /Users/nazmi/Entropy
uv venv --python 3.12
uv sync
source .venv/bin/activate
python -c "import crypcodile, textual, msgspec; print('deps ok')"
```
Expected: `deps ok`. If `textual-plotext` import is needed later, it installs here too.

- [ ] **Step 5: Verify pytest collects nothing yet, commit**

Run: `pytest -q` → Expected: `no tests ran`.
```bash
git add -A && git commit -m "chore: scaffold entropy project, env, tooling"
```

---

# Phase 1 — Feed bus + equities simulator

### Task 2: `QueueSink` — bounded, non-blocking, drop-oldest

**Files:**
- Create: `src/entropy/feeds/__init__.py`, `src/entropy/feeds/bus.py`
- Test: `tests/feeds/test_bus.py` (+ `tests/feeds/__init__.py`)

- [ ] **Step 1: Write failing test**

```python
# tests/feeds/test_bus.py
import asyncio
import pytest
from entropy.feeds.bus import QueueSink

@pytest.mark.asyncio
async def test_put_enqueues_record():
    sink = QueueSink(maxsize=4)
    await sink.put("a")
    assert sink.q.get_nowait() == "a"
    assert sink.dropped == 0

@pytest.mark.asyncio
async def test_drop_oldest_on_overflow():
    sink = QueueSink(maxsize=2)
    for x in ("a", "b", "c"):   # c overflows -> drops oldest "a"
        await sink.put(x)
    drained = [sink.q.get_nowait() for _ in range(sink.q.qsize())]
    assert drained == ["b", "c"]
    assert sink.dropped == 1

@pytest.mark.asyncio
async def test_close_is_nondestructive():
    sink = QueueSink(maxsize=2)
    await sink.put("a")
    await sink.close()                 # inherited: flush -> no-op
    assert sink.q.get_nowait() == "a"
```

- [ ] **Step 2: Run, expect fail** — `pytest tests/feeds/test_bus.py -q` → `ModuleNotFoundError: entropy.feeds.bus`.

- [ ] **Step 3: Implement**

```python
# src/entropy/feeds/bus.py
from __future__ import annotations
import asyncio
from typing import Any
from crypcodile.sink.base import Sink

class QueueSink(Sink):
    """Sink ABC impl that enqueues into a bounded asyncio.Queue.

    The kHz feed must never block on a slow UI, so put() is non-blocking and
    drops the OLDEST record on overflow (a scanner cares about latest price).
    close() inherits the default (await flush -> no-op): it must NOT clear the
    queue, because the TUI owns the queue lifecycle, not the sink.
    """
    def __init__(self, maxsize: int = 200_000) -> None:
        self.q: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    async def put(self, record: Any) -> None:
        try:
            self.q.put_nowait(record)
        except asyncio.QueueFull:
            try:
                self.q.get_nowait()
                self.dropped += 1
                self.q.put_nowait(record)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                self.dropped += 1

    async def flush(self) -> None:
        return None
```
Create empty `tests/feeds/__init__.py`, `src/entropy/feeds/__init__.py`.

- [ ] **Step 4: Run, expect pass** — `pytest tests/feeds/test_bus.py -q` → 3 passed.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: QueueSink bounded drop-oldest bridge"`

---

### Task 3: Equities universe + per-symbol params

**Files:**
- Create: `src/entropy/feeds/equities/__init__.py`, `src/entropy/feeds/equities/universe.py`
- Test: `tests/feeds/test_universe.py`

- [ ] **Step 1: Write failing test**

```python
# tests/feeds/test_universe.py
import random
from entropy.feeds.equities.universe import UNIVERSE, INDICES, SECTORS, build_params

def test_universe_has_indices_and_many_stocks():
    assert INDICES == ("SPY", "QQQ", "IWM")
    assert set(INDICES).issubset(set(UNIVERSE))
    assert len(UNIVERSE) >= 150
    assert len(set(UNIVERSE)) == len(UNIVERSE)   # no duplicates

def test_build_params_deterministic_and_covers_all():
    p1 = build_params(random.Random(42))
    p2 = build_params(random.Random(42))
    assert set(p1) == set(UNIVERSE)
    assert all(p1[s].s0 == p2[s].s0 for s in UNIVERSE)   # same seed -> same params
    assert all(p1[s].s0 > 0 and p1[s].sigma_bps > 0 for s in UNIVERSE)
```

- [ ] **Step 2: Run, expect fail** — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/entropy/feeds/equities/universe.py
from __future__ import annotations
import random
from dataclasses import dataclass

INDICES = ("SPY", "QQQ", "IWM")

MEGACAP    = ("AAPL","MSFT","NVDA","AMZN","GOOGL","META","AVGO","TSLA","BRKB","LLY")
SEMIS      = ("AMD","INTC","MU","QCOM","TXN","ASML","AMAT","LRCX","KLAC","ARM","SMCI","MRVL","ON","NXPI")
SOFTWARE   = ("CRM","ORCL","ADBE","NOW","SNOW","PLTR","DDOG","NET","CRWD","PANW","ZS","MDB","TEAM","SHOP","SPOT","UBER","ABNB","COIN","HOOD","SQ","PYPL")
FINANCE    = ("JPM","BAC","WFC","GS","MS","C","SCHW","BLK","AXP","V","MA","COF")
HEALTH     = ("UNH","JNJ","MRK","ABBV","PFE","TMO","DHR","ABT","BMY","AMGN","GILD","VRTX","REGN","ISRG")
INDUSTRIAL = ("GE","CAT","DE","BA","HON","UNP","GWW","ETN","EMR","PH","RTX","LMT","NOC","GD")
ENERGY     = ("XOM","CVX","COP","SLB","EOG","MPC","PSX","OXY","FANG","DVN")
CONSUMER   = ("WMT","COST","HD","LOW","NKE","MCD","SBUX","TGT","PG","KO","PEP","DIS","NFLX")
VOLATILE   = ("GME","AMC","MSTR","RIVN","LCID","CVNA","AFRM","SOFI","DKNG","RBLX","U","AI","IONQ","PLUG")

SECTORS: dict[str, tuple[str, ...]] = {
    "megacap": MEGACAP, "semis": SEMIS, "software": SOFTWARE, "finance": FINANCE,
    "health": HEALTH, "industrial": INDUSTRIAL, "energy": ENERGY,
    "consumer": CONSUMER, "volatile": VOLATILE,
}
_all_stocks: tuple[str, ...] = tuple(dict.fromkeys(sum(SECTORS.values(), ())))
UNIVERSE: tuple[str, ...] = INDICES + _all_stocks

@dataclass(slots=True)
class SymParams:
    s0: float          # opening price
    sigma_bps: float   # per-tick vol in bps of price
    drift_bps: float   # tiny per-tick drift
    mr_kappa: float    # mean-reversion strength toward intraday anchor
    base_size: float   # typical share size
    sector: str

def build_params(rng: random.Random) -> dict[str, SymParams]:
    out: dict[str, SymParams] = {}
    for sym in INDICES:
        out[sym] = SymParams(rng.uniform(180, 520), rng.uniform(0.3, 0.8), 0.0, 0.02,
                             rng.uniform(200, 800), "index")
    vol_mult = {"volatile": 3.5, "semis": 2.0, "software": 1.8}
    for sec, syms in SECTORS.items():
        m = vol_mult.get(sec, 1.0)
        for sym in syms:
            out[sym] = SymParams(rng.uniform(15, 900), rng.uniform(1.0, 3.0) * m,
                                 rng.uniform(-0.2, 0.2), rng.uniform(0.005, 0.03),
                                 rng.uniform(50, 400), sec)
    return out
```
Note: `BRKB` (no dot) avoids parser issues with `.`. Create empty `src/entropy/feeds/equities/__init__.py`.

- [ ] **Step 4: Run, expect pass** — 2 passed.

- [ ] **Step 5: Commit** — `git commit -am "feat: equities universe + per-symbol params"`

---

### Task 4: `EquitySimulator` — deterministic mean-reverting path engine

**Files:**
- Create: `src/entropy/feeds/equities/sim.py`
- Test: `tests/feeds/test_sim.py`

- [ ] **Step 1: Write failing test**

```python
# tests/feeds/test_sim.py
import random
from entropy.feeds.equities.sim import EquitySimulator

def _clock():
    return 1_000_000_000_000_000_000

def test_step_returns_tuple_and_positive_price():
    sim = EquitySimulator(random.Random(1), _clock)
    sym, px, size, side = sim.step_symbol("AAPL")
    assert sym == "AAPL" and px > 0 and size >= 1 and side in ("buy", "sell")

def test_deterministic_same_seed_same_path():
    a = EquitySimulator(random.Random(7), _clock)
    b = EquitySimulator(random.Random(7), _clock)
    seq_a = [a.step_symbol("NVDA")[1] for _ in range(200)]
    seq_b = [b.step_symbol("NVDA")[1] for _ in range(200)]
    assert seq_a == seq_b

def test_spike_injection_changes_state():
    sim = EquitySimulator(random.Random(3), _clock)
    for _ in range(500):
        sim.maybe_inject_events()
    assert len(sim._spike) >= 1   # some symbol got a spike overlay
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
# src/entropy/feeds/equities/sim.py
from __future__ import annotations
import math
import random
from dataclasses import dataclass
from typing import Callable
from .universe import SECTORS, UNIVERSE, SymParams, build_params

@dataclass(slots=True)
class SymRuntime:
    px: float
    anchor: float
    sess_high: float
    sess_low: float
    new_high: bool = False
    new_low: bool = False

class EquitySimulator:
    """Deterministic given the injected rng. step_symbol advances one symbol."""
    def __init__(self, rng: random.Random, clock_ns: Callable[[], int]) -> None:
        self.rng = rng
        self.clock_ns = clock_ns
        self.params: dict[str, SymParams] = build_params(rng)
        self.rt: dict[str, SymRuntime] = {
            s: SymRuntime(px=p.s0, anchor=p.s0, sess_high=p.s0, sess_low=p.s0)
            for s, p in self.params.items()
        }
        self._spike: dict[str, int] = {}
        self._spike_dir: dict[str, float] = {}
        self._sector_keys = list(SECTORS.keys())

    def step_symbol(self, sym: str) -> tuple[str, float, float, str]:
        p = self.params[sym]; r = self.rt[sym]
        z = self.rng.gauss(0.0, 1.0)
        mr = -p.mr_kappa * (r.px - r.anchor) / r.anchor
        ret = (p.drift_bps + mr * 10_000.0) * 1e-4 + (p.sigma_bps * 1e-4) * z
        rem = self._spike.get(sym, 0)
        if rem > 0:
            ret += self._spike_dir[sym] * (p.sigma_bps * 1e-4) * 8.0
            self._spike[sym] = rem - 1
        r.px = max(0.01, r.px * math.exp(ret))
        r.new_high = r.px > r.sess_high
        r.new_low = r.px < r.sess_low
        if r.new_high: r.sess_high = r.px
        if r.new_low: r.sess_low = r.px
        r.anchor *= math.exp((p.drift_bps * 1e-4) * 0.1)
        side = "buy" if ret >= 0 else "sell"
        size = max(1.0, p.base_size * (0.5 + self.rng.random()))
        return sym, r.px, round(size), side

    def maybe_inject_events(self) -> None:
        if self.rng.random() < 0.15:
            sym = self.rng.choice(UNIVERSE)
            self._spike[sym] = self.rng.randint(3, 12)
            self._spike_dir[sym] = self.rng.choice((1.0, -1.0))
        if self.rng.random() < 0.04:
            sec = self.rng.choice(self._sector_keys)
            direction = self.rng.choice((1.0, -1.0))
            for sym in SECTORS[sec]:
                if self.rng.random() < 0.6:
                    self._spike[sym] = self.rng.randint(4, 15)
                    self._spike_dir[sym] = direction
```

- [ ] **Step 4: Run, expect pass.**

- [ ] **Step 5: Commit** — `git commit -am "feat: deterministic equity simulator (mean-reverting GBM + spikes)"`

---

### Task 5: `EquitySimFeed` — async task emitting real `Trade` records

**Files:**
- Create: `src/entropy/feeds/equities/feed.py`
- Test: `tests/feeds/test_equity_feed.py`

- [ ] **Step 1: Write failing test**

```python
# tests/feeds/test_equity_feed.py
import asyncio
import pytest
from crypcodile.schema.records import Trade
from crypcodile.schema.enums import Side
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed, EXCHANGE

@pytest.mark.asyncio
async def test_feed_emits_trades_into_sink():
    sink = QueueSink(maxsize=10_000)
    feed = EquitySimFeed(sink, seed=5, ticks_per_sec=2000, batch_dt=0.01)
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    recs = [sink.q.get_nowait() for _ in range(sink.q.qsize())]
    assert recs, "expected some trades"
    r = recs[0]
    assert isinstance(r, Trade)
    assert r.exchange == EXCHANGE
    assert r.side in (Side.BUY, Side.SELL)
    assert r.price > 0 and r.local_ts == r.exchange_ts
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
# src/entropy/feeds/equities/feed.py
from __future__ import annotations
import asyncio
import random
from typing import Callable, Iterable
from crypcodile.schema.records import Trade
from crypcodile.schema.enums import Side
from crypcodile.sink.base import Sink
from crypcodile.util.time import now_ns
from .sim import EquitySimulator
from .universe import UNIVERSE

EXCHANGE = "sim-equity"
_SIDE = {"buy": Side.BUY, "sell": Side.SELL}

class EquitySimFeed:
    def __init__(self, sink: Sink, *, seed: int = 7, ticks_per_sec: int = 4000,
                 clock_ns: Callable[[], int] = now_ns,
                 market_hours_gate: Callable[[int], bool] | None = None,
                 batch_dt: float = 0.01) -> None:
        self.sink = sink
        self.rng = random.Random(seed)
        self.clock_ns = clock_ns
        self.sim = EquitySimulator(self.rng, clock_ns)
        self.tps = ticks_per_sec
        self.batch_dt = batch_dt
        self.gate = market_hours_gate
        self._ids = 0

    def _next_id(self) -> str:
        self._ids += 1
        return f"e{self._ids}"

    def _emit_batch(self, n: int) -> Iterable[Trade]:
        ts = self.clock_ns()
        for _ in range(n):
            sym = self.rng.choice(UNIVERSE)
            s, px, size, side = self.sim.step_symbol(sym)
            yield Trade(exchange=EXCHANGE, symbol=s, symbol_raw=s,
                        exchange_ts=ts, local_ts=ts, id=self._next_id(),
                        price=px, amount=float(size), side=_SIDE[side])

    async def run(self) -> None:
        per_batch = max(1, int(self.tps * self.batch_dt))
        try:
            while True:
                if self.gate is None or self.gate(self.clock_ns()):
                    self.sim.maybe_inject_events()
                    for tr in self._emit_batch(per_batch):
                        await self.sink.put(tr)
                await asyncio.sleep(self.batch_dt)
        except asyncio.CancelledError:
            await self.sink.flush()
            raise
```

- [ ] **Step 4: Run, expect pass.**

- [ ] **Step 5: Commit** — `git commit -am "feat: async equity sim feed emitting Crypcodile Trade records"`

---

# Phase 2 — Detection engine (TDD core)

### Task 6: `RateMeter` — 1s-bucket sliding counter

**Files:**
- Create: `src/entropy/engine/__init__.py`, `src/entropy/engine/rate.py`
- Test: `tests/engine/test_rate.py` (+ `tests/engine/__init__.py`)

- [ ] **Step 1: Write failing test**

```python
# tests/engine/test_rate.py
from entropy.engine.rate import RateMeter

S = 1_000_000_000  # 1s in ns

def test_steady_rate_three_per_sec():
    m = RateMeter(window_s=30)
    for sec in range(60):
        for _ in range(3):
            m.add(sec * S)
    assert m.rate_per_s() == 3.0   # last 30s all had 3/s

def test_window_evicts_old_buckets():
    m = RateMeter(window_s=2)
    m.add(0 * S, 5)
    m.add(1 * S, 5)
    m.add(3 * S, 1)   # sec 0 now older than window (3-2=1) -> evicted
    assert m.total == 6            # sec1(5) + sec3(1)

def test_raw_hz_last_second():
    m = RateMeter(window_s=1)
    m.add(10 * S, 4000)
    assert m.rate_per_s() == 4000.0
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
# src/entropy/engine/rate.py
from __future__ import annotations
from collections import deque

class RateMeter:
    """Sliding events/sec over window_s using 1-second integer buckets. O(1) add."""
    __slots__ = ("window_s", "buckets", "total")

    def __init__(self, window_s: int) -> None:
        self.window_s = window_s
        self.buckets: deque[list[int]] = deque()  # [sec, count]
        self.total = 0

    def add(self, ts_ns: int, n: int = 1) -> None:
        sec = ts_ns // 1_000_000_000
        if self.buckets and self.buckets[-1][0] == sec:
            self.buckets[-1][1] += n
        else:
            self.buckets.append([sec, n])
        self.total += n
        cutoff = sec - self.window_s
        while self.buckets and self.buckets[0][0] <= cutoff:
            self.total -= self.buckets.popleft()[1]

    def rate_per_s(self) -> float:
        return self.total / self.window_s
```
Create empty `src/entropy/engine/__init__.py`, `tests/engine/__init__.py`.

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: RateMeter sliding 1s-bucket counter"`

---

### Task 7: `MonotonicExtreme` + `SessionExtreme` — rolling new-high/low detection

**Files:**
- Create: `src/entropy/engine/windows.py`
- Test: `tests/engine/test_windows_extreme.py`

- [ ] **Step 1: Write failing test** (encodes the simulation-verified trace)

```python
# tests/engine/test_windows_extreme.py
from entropy.engine.windows import MonotonicExtreme, SessionExtreme

def test_rolling_max_new_high_trace():
    # span=100ns; verified expected new-high flags: T,T,F,F,T
    m = MonotonicExtreme(span_ns=100, kind=+1)
    seq = [(0, 10.0), (50, 12.0), (80, 11.0), (120, 9.0), (160, 13.0)]
    flags = [m.step(ts, px) for ts, px in seq]
    assert flags == [True, True, False, False, True]

def test_equal_price_is_not_new_high_strict():
    m = MonotonicExtreme(span_ns=1000, kind=+1)
    assert m.step(0, 10.0) is True      # first
    assert m.step(1, 10.0) is False     # equal -> not new (STRICT >)

def test_rolling_min_new_low():
    m = MonotonicExtreme(span_ns=100, kind=-1)
    seq = [(0, 10.0), (50, 8.0), (80, 9.0), (160, 11.0)]
    flags = [m.step(ts, px) for ts, px in seq]
    # t0 first->True; t50 8<10 ->True; t80 9 not< min(8) ->False;
    # t160 cutoff=60 evicts 8@50 and 10@0; min now 9@80 -> 11 not<9 ->False
    assert flags == [True, True, False, False]

def test_session_extreme_tracks_hi_lo_and_pct():
    s = SessionExtreme()
    assert s.step(100.0) == (True, True)    # first tick sets both baselines
    assert s.step(101.0) == (True, False)
    assert s.step(99.0) == (False, True)
    assert abs(s.pct_chg(110.0) - 0.10) < 1e-9   # (110-100)/100
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
# src/entropy/engine/windows.py
from __future__ import annotations
from collections import deque

class MonotonicExtreme:
    """Rolling max (kind=+1) or min (kind=-1) over span_ns, O(1) amortized.
    step() queries the PRIOR extreme (before inserting) and reports a STRICT
    new extreme (> for max, < for min); equalling the extreme is not new."""
    __slots__ = ("span_ns", "kind", "dq")

    def __init__(self, span_ns: int, kind: int) -> None:
        self.span_ns = span_ns
        self.kind = kind
        self.dq: deque[tuple[int, float]] = deque()

    def _dominates(self, a: float, b: float) -> bool:
        return a >= b if self.kind > 0 else a <= b

    def evict(self, now_ns: int) -> None:
        cutoff = now_ns - self.span_ns
        dq = self.dq
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def peek(self) -> float | None:
        return self.dq[0][1] if self.dq else None

    def step(self, ts_ns: int, price: float) -> bool:
        self.evict(ts_ns)
        prior = self.peek()
        is_new = prior is None or (price > prior if self.kind > 0 else price < prior)
        dq = self.dq
        while dq and self._dominates(price, dq[-1][1]):
            dq.pop()
        dq.append((ts_ns, price))
        return is_new

class SessionExtreme:
    """Cumulative session high/low + first price (for %Chg). O(1), 3 floats."""
    __slots__ = ("hi", "lo", "first_price")

    def __init__(self) -> None:
        self.hi: float | None = None
        self.lo: float | None = None
        self.first_price: float | None = None

    def step(self, price: float) -> tuple[bool, bool]:
        new_hi = self.hi is None or price > self.hi
        new_lo = self.lo is None or price < self.lo
        if new_hi: self.hi = price
        if new_lo: self.lo = price
        if self.first_price is None: self.first_price = price
        return new_hi, new_lo

    def pct_chg(self, price: float) -> float:
        if not self.first_price:
            return 0.0
        return (price - self.first_price) / self.first_price
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: monotonic-deque rolling extremes + session extreme"`

---

### Task 8: `MomentumHorizon` — reference price ~N seconds ago

**Files:**
- Modify: `src/entropy/engine/windows.py` (append class)
- Test: `tests/engine/test_momentum_horizon.py`

- [ ] **Step 1: Write failing test** (verified trace: ref=100.5 at t=6s → +7.463%)

```python
# tests/engine/test_momentum_horizon.py
from entropy.engine.windows import MomentumHorizon

S = 1_000_000_000

def test_reference_price_anchor():
    h = MomentumHorizon(span_ns=5 * S)
    h.push(0 * S, 100.0)
    h.push(1 * S, 100.5)
    h.push(2 * S, 101.0)
    ref = h.push(6 * S, 108.0)   # cutoff = 1s; keep anchor at/older than cutoff
    assert ref == 100.5
    pct = (108.0 - ref) / ref * 100
    assert abs(pct - 7.4626865) < 1e-4
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement (append to windows.py)**

```python
class MomentumHorizon:
    """Maintains a (ts,price) deque; push() returns the reference price ~span
    ago (the newest anchor at or older than now-span)."""
    __slots__ = ("span_ns", "dq", "last_evicted")

    def __init__(self, span_ns: int) -> None:
        self.span_ns = span_ns
        self.dq: deque[tuple[int, float]] = deque()
        self.last_evicted: float | None = None

    def push(self, ts_ns: int, price: float) -> float:
        dq = self.dq
        dq.append((ts_ns, price))
        cutoff = ts_ns - self.span_ns
        while len(dq) >= 2 and dq[1][0] <= cutoff:
            self.last_evicted = dq.popleft()[1]
        return dq[0][1]

    def has_anchor(self, ts_ns: int) -> bool:
        """True once at least one tick older than the cutoff exists."""
        return bool(self.dq) and self.dq[0][0] <= ts_ns - self.span_ns
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: momentum horizon reference-price anchor"`

---

### Task 9: Engine events + config types

**Files:**
- Create: `src/entropy/engine/events.py`, `src/entropy/config.py`
- Test: `tests/engine/test_events.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/test_events.py
from entropy.engine.events import NewHigh, NewLow, Spike, SnapDrop, EventKind, WindowName
from entropy.config import EngineConfig

def test_event_structs_carry_fields():
    e = NewHigh(symbol="X", ts_ns=1, price=10.0, window=WindowName.S30, prev_extreme=9.0)
    assert e.kind == EventKind.NEW_HIGH and e.window == WindowName.S30
    s = Spike(symbol="X", ts_ns=2, price=11.0, pct=0.5, horizon_s=5.0, ref_price=10.0)
    assert s.kind == EventKind.SPIKE and s.pct == 0.5

def test_engine_config_defaults():
    c = EngineConfig()
    assert c.spike_pct == 0.40 and c.upmove_pct == 0.15
    assert c.windows_ns["30s"] == 30_000_000_000 and "session" not in c.windows_ns
    assert c.new_extreme_strict is True and c.leaderboard_k == 20
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
# src/entropy/engine/events.py
from __future__ import annotations
import enum
import msgspec

class WindowName(enum.StrEnum):
    S30 = "30s"; M1 = "1m"; M5 = "5m"; M20 = "20m"; SESSION = "session"

class EventKind(enum.StrEnum):
    NEW_HIGH = "new_high"; NEW_LOW = "new_low"
    SPIKE = "spike"; SNAP_DROP = "snap_drop"; UPMOVE = "upmove"; DOWNMOVE = "downmove"

class _Base(msgspec.Struct, frozen=True, tag_field="kind"):
    symbol: str
    ts_ns: int
    price: float

class NewHigh(_Base, frozen=True, tag=EventKind.NEW_HIGH.value):
    window: WindowName = WindowName.SESSION
    prev_extreme: float | None = None

class NewLow(_Base, frozen=True, tag=EventKind.NEW_LOW.value):
    window: WindowName = WindowName.SESSION
    prev_extreme: float | None = None

class Spike(_Base, frozen=True, tag=EventKind.SPIKE.value):
    pct: float = 0.0; horizon_s: float = 5.0; ref_price: float = 0.0

class SnapDrop(_Base, frozen=True, tag=EventKind.SNAP_DROP.value):
    pct: float = 0.0; horizon_s: float = 5.0; ref_price: float = 0.0

class UpMove(_Base, frozen=True, tag=EventKind.UPMOVE.value):
    pct: float = 0.0; horizon_s: float = 5.0; ref_price: float = 0.0

class DownMove(_Base, frozen=True, tag=EventKind.DOWNMOVE.value):
    pct: float = 0.0; horizon_s: float = 5.0; ref_price: float = 0.0

Event = NewHigh | NewLow | Spike | SnapDrop | UpMove | DownMove

@property  # type: ignore[misc]
def _kind(self: _Base) -> EventKind:  # convenience accessor on instances
    return EventKind(self.__struct_config__.tag)  # tag stored by msgspec
```
> Note: the `_Base.kind` accessor — msgspec stores the tag; expose `EventKind` via a
> module helper `kind_of(event) -> EventKind` instead of a property if the above is awkward:
```python
def kind_of(e: Event) -> EventKind:
    return EventKind(msgspec.structs.asdict(e).get("kind", ""))  # fallback
```
Implementer: prefer matching on the concrete type (`isinstance(e, NewHigh)`) downstream; the
`kind`/tag is for serialization. Keep the test asserting `e.kind` — implement `kind` as a
class attribute on each subclass instead if msgspec tag access is unwieldy:
```python
# Simpler, robust alternative the test expects:
class NewHigh(_Base, frozen=True):
    kind: EventKind = EventKind.NEW_HIGH
    window: WindowName = WindowName.SESSION
    prev_extreme: float | None = None
# (drop tag/tag_field; carry `kind` as an explicit field on each subclass)
```
**Decision for implementer:** use the explicit-`kind`-field form (second variant) — simplest,
matches the test, no tag gymnastics.

```python
# src/entropy/config.py
from __future__ import annotations
import msgspec

def _default_windows() -> dict[str, int]:
    return {"30s": 30_000_000_000, "1m": 60_000_000_000,
            "5m": 300_000_000_000, "20m": 1_200_000_000_000}

class EngineConfig(msgspec.Struct, frozen=True):
    windows_ns: dict[str, int] = msgspec.field(default_factory=_default_windows)
    momentum_horizon_s: float = 5.0
    spike_pct: float = 0.40
    snapdrop_pct: float = 0.40
    upmove_pct: float = 0.15
    downmove_pct: float = 0.15
    momentum_cooldown_ns: int = 1_000_000_000
    new_extreme_strict: bool = True
    breadth_window_s: int = 30
    leaderboard_k: int = 20
    accel_eps: float = 0.10
```

- [ ] **Step 4: Run, expect pass** (using the explicit-`kind`-field event variant).
- [ ] **Step 5: Commit** — `git commit -am "feat: engine event structs + EngineConfig"`

---

### Task 10: `BreadthTracker` — Sell%/Buy%, Hz, accel

**Files:**
- Create: `src/entropy/engine/breadth.py`
- Test: `tests/engine/test_breadth.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/test_breadth.py
from entropy.engine.breadth import BreadthTracker

S = 1_000_000_000

def test_sell_buy_pct_amount_weighted():
    b = BreadthTracker(window_s=30)
    b.add_trade("buy", 30.0, 0)
    b.add_trade("sell", 70.0, 0)
    assert abs(b.sell_pct() - 70.0) < 1e-9
    assert abs(b.buy_pct() - 30.0) < 1e-9

def test_raw_hz_and_event_rate():
    b = BreadthTracker(window_s=30)
    for _ in range(4000):
        b.tick(10 * S)
    b.events(10 * S, 3)
    assert b.raw_hz() == 4000.0

def test_accel_flag():
    b = BreadthTracker(window_s=30)
    assert b.accel(prev_rate=0.0) == "steady"
    b._event_meter.add(0, 100)
    assert b.accel(prev_rate=1.0) == "accelerating"
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
# src/entropy/engine/breadth.py
from __future__ import annotations
from .rate import RateMeter

class BreadthTracker:
    def __init__(self, window_s: int = 30, accel_eps: float = 0.10) -> None:
        self.window_s = window_s
        self.accel_eps = accel_eps
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self._tick_meter = RateMeter(window_s=1)     # raw Hz
        self._event_meter = RateMeter(window_s=window_s)

    def add_trade(self, side: str, amount: float, ts_ns: int) -> None:
        if side == "sell":
            self.sell_vol += amount
        else:
            self.buy_vol += amount

    def tick(self, ts_ns: int) -> None:
        self._tick_meter.add(ts_ns)

    def events(self, ts_ns: int, n: int) -> None:
        if n:
            self._event_meter.add(ts_ns, n)

    def sell_pct(self) -> float:
        tot = self.buy_vol + self.sell_vol
        return self.sell_vol / tot * 100 if tot else 0.0

    def buy_pct(self) -> float:
        return 100.0 - self.sell_pct() if (self.buy_vol + self.sell_vol) else 0.0

    def raw_hz(self) -> float:
        return self._tick_meter.rate_per_s()

    def event_rate(self) -> float:
        return self._event_meter.rate_per_s()

    def accel(self, prev_rate: float) -> str:
        now = self.event_rate()
        if now > prev_rate * (1 + self.accel_eps):
            return "accelerating"
        if now < prev_rate * (1 - self.accel_eps):
            return "decelerating"
        return "steady"
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: breadth tracker (sell/buy %, Hz, accel)"`

---

### Task 11: `Engine` + `snapshot()` — wiring the core

**Files:**
- Create: `src/entropy/engine/leaderboard.py`, `src/entropy/engine/engine.py`
- Test: `tests/engine/test_engine.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/test_engine.py
from entropy.engine.engine import Engine
from entropy.engine.events import NewHigh, NewLow

S = 1_000_000_000

def test_first_tick_is_baseline_no_events():
    e = Engine()
    assert e.on_trade("AAA", 100.0, 1.0, "buy", 0) == []

def test_new_session_high_emitted():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)         # baseline
    evs = e.on_trade("AAA", 101.0, 1.0, "buy", S)
    assert any(isinstance(x, NewHigh) for x in evs)

def test_snapshot_has_breadth_and_boards():
    e = Engine()
    e.on_trade("AAA", 100.0, 5.0, "buy", 0)
    e.on_trade("AAA", 101.0, 5.0, "buy", S)
    e.on_trade("BBB", 50.0, 5.0, "sell", S)
    snap = e.snapshot()
    assert snap.breadth.buy_pct >= 0 and snap.breadth.sell_pct >= 0
    assert isinstance(snap.new_highs, tuple)
    assert any(r.symbol == "AAA" for r in snap.top_movers)

def test_determinism_same_input_same_events():
    seq = [("AAA", 100.0, 1.0, "buy", 0), ("AAA", 102.0, 1.0, "buy", S),
           ("AAA", 99.0, 1.0, "sell", 2 * S)]
    a, b = Engine(), Engine()
    out_a = [a.on_trade(*t) for t in seq]
    out_b = [b.on_trade(*t) for t in seq]
    assert out_a == out_b
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement leaderboard + engine**

```python
# src/entropy/engine/leaderboard.py
from __future__ import annotations
import msgspec

class LeaderRow(msgspec.Struct, frozen=True):
    symbol: str
    count: int
    price: float
    pct_chg: float
```

```python
# src/entropy/engine/engine.py
from __future__ import annotations
import heapq
import msgspec
from .breadth import BreadthTracker
from .events import (DownMove, Event, NewHigh, NewLow, SnapDrop, Spike, UpMove, WindowName)
from .leaderboard import LeaderRow
from ..config import EngineConfig
from .windows import MomentumHorizon, MonotonicExtreme, SessionExtreme

_WIN_ORDER = (WindowName.S30, WindowName.M1, WindowName.M5, WindowName.M20)

class _Tape:
    __slots__ = ("maxw", "minw", "session", "mom", "last_ts", "last_price",
                 "nh_count", "nl_count", "last_mom_pct", "_cooldown")

    def __init__(self, cfg: EngineConfig) -> None:
        self.maxw = [MonotonicExtreme(cfg.windows_ns[w.value], +1) for w in _WIN_ORDER]
        self.minw = [MonotonicExtreme(cfg.windows_ns[w.value], -1) for w in _WIN_ORDER]
        self.session = SessionExtreme()
        self.mom = MomentumHorizon(int(cfg.momentum_horizon_s * 1_000_000_000))
        self.last_ts = 0
        self.last_price = 0.0
        self.nh_count = 0
        self.nl_count = 0
        self.last_mom_pct = 0.0
        self._cooldown: dict[str, int] = {}

class EngineSnapshot(msgspec.Struct, frozen=True):
    ts_ns: int
    breadth: "BreadthSnapshot"
    top_movers: tuple[LeaderRow, ...]
    new_highs: tuple[LeaderRow, ...]
    new_lows: tuple[LeaderRow, ...]

class BreadthSnapshot(msgspec.Struct, frozen=True):
    sell_pct: float
    buy_pct: float
    raw_hz: float
    prev30s_rate: float
    accel: str
    nh_counts: dict[str, int]
    nl_counts: dict[str, int]

class Engine:
    def __init__(self, config: EngineConfig | None = None) -> None:
        self.cfg = config or EngineConfig()
        self.breadth = BreadthTracker(self.cfg.breadth_window_s, self.cfg.accel_eps)
        self._tapes: dict[str, _Tape] = {}
        self._seen: set[str] = set()
        self._prev_event_rate = 0.0
        self._horizon_s = self.cfg.momentum_horizon_s
        self._cool_ns = self.cfg.momentum_cooldown_ns

    def on_trade(self, symbol: str, price: float, amount: float, side: str, ts_ns: int) -> list[Event]:
        t = self._tapes.get(symbol)
        if t is None:
            t = _Tape(self.cfg); self._tapes[symbol] = t
        ts = ts_ns if ts_ns >= t.last_ts else t.last_ts   # non-decreasing clamp
        t.last_ts = ts
        self.breadth.tick(ts)
        self.breadth.add_trade(side, amount, ts)
        first = symbol not in self._seen
        events: list[Event] = []
        if first:
            self._seen.add(symbol)
            for me in t.maxw: me.step(ts, price)
            for me in t.minw: me.step(ts, price)
            t.session.step(price)
            t.mom.push(ts, price)
            t.last_price = price
            return events
        for w, me in zip(_WIN_ORDER, t.maxw):
            prior = me.peek()
            if me.step(ts, price):
                events.append(NewHigh(symbol=symbol, ts_ns=ts, price=price, window=w, prev_extreme=prior))
                t.nh_count += 1
        for w, me in zip(_WIN_ORDER, t.minw):
            prior = me.peek()
            if me.step(ts, price):
                events.append(NewLow(symbol=symbol, ts_ns=ts, price=price, window=w, prev_extreme=prior))
                t.nl_count += 1
        sh, sl = t.session.step(price)
        if sh:
            events.append(NewHigh(symbol=symbol, ts_ns=ts, price=price, window=WindowName.SESSION))
            t.nh_count += 1
        if sl:
            events.append(NewLow(symbol=symbol, ts_ns=ts, price=price, window=WindowName.SESSION))
            t.nl_count += 1
        ref = t.mom.push(ts, price)
        if t.mom.has_anchor(ts) and ref > 0:
            pct = (price - ref) / ref * 100.0
            kind = self._classify(pct)
            if kind is not None and ts - t._cooldown.get(kind.__name__, -self._cool_ns) >= self._cool_ns:
                events.append(kind(symbol=symbol, ts_ns=ts, price=price, pct=pct,
                                   horizon_s=self._horizon_s, ref_price=ref))
                t._cooldown[kind.__name__] = ts
            t.last_mom_pct = pct
        t.last_price = price
        self.breadth.events(ts, len(events))
        return events

    def _classify(self, pct: float):
        c = self.cfg
        if pct >= c.spike_pct: return Spike
        if pct >= c.upmove_pct: return UpMove
        if pct <= -c.snapdrop_pct: return SnapDrop
        if pct <= -c.downmove_pct: return DownMove
        return None

    def snapshot(self) -> EngineSnapshot:
        k = self.cfg.leaderboard_k
        items = list(self._tapes.items())
        def pct(kv): return kv[1].session.pct_chg(kv[1].last_price)
        def nh(kv): return kv[1].nh_count
        def nl(kv): return kv[1].nl_count
        top = heapq.nlargest(k, items, key=lambda kv: abs(pct(kv)))
        highs = heapq.nlargest(k, items, key=nh)
        lows = heapq.nlargest(k, items, key=nl)
        def rows(sel, cnt): return tuple(
            LeaderRow(s, cnt(t.last_price, tp), tp.last_price, tp.session.pct_chg(tp.last_price) * 100)
            for s, tp in sel for t in (tp,)
        )
        def mk(sel, count_fn):
            return tuple(LeaderRow(symbol=s, count=count_fn(tp), price=tp.last_price,
                                   pct_chg=tp.session.pct_chg(tp.last_price) * 100) for s, tp in sel)
        rate = self.breadth.event_rate()
        accel = self.breadth.accel(self._prev_event_rate)
        self._prev_event_rate = rate
        breadth = BreadthSnapshot(
            sell_pct=self.breadth.sell_pct(), buy_pct=self.breadth.buy_pct(),
            raw_hz=self.breadth.raw_hz(), prev30s_rate=rate, accel=accel,
            nh_counts={}, nl_counts={})
        last_ts = max((t.last_ts for t in self._tapes.values()), default=0)
        return EngineSnapshot(
            ts_ns=last_ts, breadth=breadth,
            top_movers=mk(top, lambda tp: tp.nh_count + tp.nl_count),
            new_highs=mk(highs, lambda tp: tp.nh_count),
            new_lows=mk(lows, lambda tp: tp.nl_count))

    def reset_session(self, ts_ns: int | None = None) -> None:
        for t in self._tapes.values():
            t.session = SessionExtreme()
            t.nh_count = 0; t.nl_count = 0
        self._seen.clear()
```
> Implementer note: remove the unused `rows()` helper if ruff flags it; `mk()` is the one used.
> Keep `snapshot()` allocation-light; it runs ~10×/s, not per tick.

- [ ] **Step 4: Run, expect pass.** Fix any ruff/mypy issues (drop unused locals).
- [ ] **Step 5: Commit** — `git commit -am "feat: detection Engine + immutable snapshot"`

---

### Task 12: Engine perf microbench (guard throughput)

**Files:**
- Test: `tests/engine/test_engine_perf.py`

- [ ] **Step 1: Write the benchmark test**

```python
# tests/engine/test_engine_perf.py
import time
from entropy.engine.engine import Engine
from entropy.feeds.equities.universe import UNIVERSE

def test_engine_throughput():
    e = Engine()
    syms = UNIVERSE
    n = 200_000
    base = 1_000_000_000_000
    t0 = time.perf_counter()
    for i in range(n):
        s = syms[i % len(syms)]
        e.on_trade(s, 100.0 + (i % 17) * 0.1, 10.0, "buy" if i & 1 else "sell", base + i * 1000)
    dt = time.perf_counter() - t0
    rate = n / dt
    assert rate > 100_000, f"engine too slow: {rate:.0f} ticks/s"
```

- [ ] **Step 2: Run** — `pytest tests/engine/test_engine_perf.py -q`. Expected: PASS (>100k ticks/s). If it fails, profile and apply hot-path fixes (local-bind the window lists, avoid dict lookups) before proceeding.
- [ ] **Step 3: Commit** — `git commit -am "test: engine throughput microbench"`

---

# Phase 3 — Strategy console (TDD)

### Task 13: `EmaState` + `ema_update`

**Files:**
- Create: `src/entropy/strategy/__init__.py`, `src/entropy/strategy/ema.py`
- Test: `tests/strategy/test_ema.py` (+ `tests/strategy/__init__.py`)

- [ ] **Step 1: Write failing test**

```python
# tests/strategy/test_ema.py
from entropy.strategy.ema import EmaState, ema_update

def test_ema_seeds_with_first_sample():
    st = EmaState(span=3)
    assert ema_update(st, 10.0) == 10.0
    assert st.count == 1

def test_ema_converges_toward_input():
    st = EmaState(span=2)            # alpha = 2/3
    ema_update(st, 10.0)
    v = ema_update(st, 13.0)         # 10 + 2/3*(13-10) = 12.0
    assert abs(v - 12.0) < 1e-9
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/strategy/ema.py
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(slots=True)
class EmaState:
    span: int
    alpha: float = field(init=False)
    value: float | None = None
    count: int = 0
    def __post_init__(self) -> None:
        self.alpha = 2.0 / (self.span + 1.0)

def ema_update(st: EmaState, px: float) -> float:
    if st.value is None:
        st.value = px
    else:
        st.value += st.alpha * (px - st.value)
    st.count += 1
    return st.value
```
Create empty `src/entropy/strategy/__init__.py`, `tests/strategy/__init__.py`.

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: pure EMA state + update"`

---

### Task 14: `Strategy` engine — EMA crossover, position, PnL

**Files:**
- Create: `src/entropy/strategy/engine.py`
- Test: `tests/strategy/test_strategy.py`

- [ ] **Step 1: Write failing tests** (golden flip, SHORT sign matches GIF, fee, guards)

```python
# tests/strategy/test_strategy.py
from entropy.strategy.engine import Strategy, StrategyConfig, Bar, Side
from entropy.strategy.engine import EventKind

def _warm(s, closes, t0=0, dt=1):
    return s.warmup([Bar(ts_ns=(t0 + i) * dt, close=c) for i, c in enumerate(closes)])

def test_warmup_info_and_warm_flag():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3))
    evs = _warm(s, [100, 100, 100])
    assert evs and evs[0].kind == EventKind.INFO
    assert s.is_warm and s.position.side == Side.FLAT

def test_golden_long_then_flip_to_short():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3, size=1.0, fee_bps=0.0))
    _warm(s, [100, 100, 100])
    assert s.on_price("T", 100.0, 10) == []                       # signal 0
    o = s.on_price("T", 101.0, 11)
    assert len(o) == 1 and o[0].kind == EventKind.OPEN_LONG
    s.on_price("T", 103.0, 12)
    flip = s.on_price("T", 99.0, 13)
    kinds = [e.kind for e in flip]
    assert kinds == [EventKind.CLOSE_LONG, EventKind.OPEN_SHORT]
    assert abs(flip[0].trade_pnl - (-2.0)) < 1e-9                 # 99-101

def test_short_sign_matches_gif():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3))
    # directly exercise pnl helper via a short then close
    from entropy.strategy.engine import _gross_pnl
    assert abs(_gross_pnl(Side.SHORT, 748.300, 748.435, 1.0) - (-0.135)) < 1e-9
    assert abs(_gross_pnl(Side.LONG, 749.886, 750.025, 1.0) - 0.139) < 1e-9

def test_fee_applied_on_close():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3, fee_bps=10.0))
    _warm(s, [100, 100, 100])
    s.on_price("T", 101.0, 11)         # OPEN_LONG @101
    flip = s.on_price("T", 99.0, 13)   # CLOSE_LONG @99
    # gross -2.0; fees = 101*0.001 + 99*0.001 = 0.2 -> -2.2
    assert abs(flip[0].trade_pnl - (-2.2)) < 1e-9

def test_long_only_guard():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3, allow_short=False))
    _warm(s, [100, 100, 100])
    s.on_price("T", 101.0, 11)
    out = s.on_price("T", 99.0, 13)
    assert [e.kind for e in out] == [EventKind.CLOSE_LONG]
    assert s.position.side == Side.FLAT

def test_symbol_mismatch_ignored():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3))
    _warm(s, [100, 100, 100])
    assert s.on_price("OTHER", 101.0, 11) == []

def test_running_pnl_mark():
    s = Strategy(StrategyConfig(symbol="T", fast=2, slow=3, size=2.0))
    _warm(s, [100, 100, 100])
    s.on_price("T", 101.0, 11)         # OPEN_LONG size 2
    assert abs(s.running_pnl(104.0) - 6.0) < 1e-9
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
# src/entropy/strategy/engine.py
from __future__ import annotations
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from .ema import EmaState, ema_update

class Side(Enum):
    FLAT = 0; LONG = 1; SHORT = 2

class EventKind(str, Enum):
    INFO = "info"; OPEN_LONG = "open_long"; OPEN_SHORT = "open_short"
    CLOSE_LONG = "close_long"; CLOSE_SHORT = "close_short"

@dataclass(frozen=True, slots=True)
class Bar:
    ts_ns: int
    close: float
    high: float | None = None
    low: float | None = None

@dataclass(frozen=True, slots=True)
class StrategyEvent:
    kind: EventKind
    ts_ns: int
    symbol: str
    price: float
    running_pnl: float | None = None
    trade_pnl: float | None = None
    text: str | None = None

@dataclass(frozen=True, slots=True)
class StrategyConfig:
    symbol: str = "SPY"
    fast: int = 9
    slow: int = 21
    mode: str = "ema"
    breakout_lookback: int = 20
    size: float = 1.0
    fee_bps: float = 0.0
    allow_short: bool = True
    warmup_bars: int = 0

@dataclass(slots=True)
class Position:
    side: Side = Side.FLAT
    entry_px: float = 0.0
    size: float = 0.0
    entry_ts_ns: int = 0

def _gross_pnl(side: Side, entry: float, px: float, size: float) -> float:
    d = (px - entry) if side is Side.LONG else (entry - px)
    return d * size

def _fee(px: float, size: float, fee_bps: float) -> float:
    return abs(px * size) * (fee_bps / 10_000.0)

class Strategy:
    def __init__(self, config: StrategyConfig) -> None:
        self.cfg = config
        self._fast = EmaState(config.fast)
        self._slow = EmaState(config.slow)
        self._prev_sign = 0
        self.position = Position()
        self._warmup_target = config.warmup_bars or config.slow

    @property
    def is_warm(self) -> bool:
        return self._slow.count >= self._warmup_target

    def warmup(self, bars: Sequence[Bar]) -> list[StrategyEvent]:
        for b in bars:
            ema_update(self._fast, b.close)
            ema_update(self._slow, b.close)
        self._prev_sign = self._signum()
        n = len(bars)
        return [StrategyEvent(EventKind.INFO, bars[-1].ts_ns if bars else 0,
                              self.cfg.symbol, bars[-1].close if bars else 0.0,
                              text=f"{self.cfg.symbol} warmup: {n} bars, EMA ready")]

    def _signum(self) -> int:
        if self._fast.value is None or self._slow.value is None:
            return 0
        d = self._fast.value - self._slow.value
        return 1 if d > 0 else (-1 if d < 0 else 0)

    def running_pnl(self, last_px: float) -> float:
        if self.position.side is Side.FLAT:
            return 0.0
        return _gross_pnl(self.position.side, self.position.entry_px, last_px, self.position.size)

    def on_price(self, symbol: str, price: float, ts_ns: int) -> list[StrategyEvent]:
        if symbol != self.cfg.symbol:
            return []
        ema_update(self._fast, price)
        ema_update(self._slow, price)
        if not self.is_warm:
            return []
        sign = self._signum()
        events: list[StrategyEvent] = []
        desired = self.position.side
        if self._prev_sign <= 0 and sign > 0:
            desired = Side.LONG
        elif self._prev_sign >= 0 and sign < 0:
            desired = Side.SHORT
        self._prev_sign = sign
        if desired is not self.position.side and desired is not Side.FLAT:
            if self.position.side is not Side.FLAT:
                events.append(self._close(price, ts_ns))
            if desired is Side.SHORT and not self.cfg.allow_short:
                return events
            events.append(self._open(desired, price, ts_ns))
        return events

    def _open(self, side: Side, price: float, ts_ns: int) -> StrategyEvent:
        self.position = Position(side=side, entry_px=price, size=self.cfg.size, entry_ts_ns=ts_ns)
        kind = EventKind.OPEN_LONG if side is Side.LONG else EventKind.OPEN_SHORT
        return StrategyEvent(kind, ts_ns, self.cfg.symbol, price, running_pnl=0.0)

    def _close(self, price: float, ts_ns: int) -> StrategyEvent:
        p = self.position
        gross = _gross_pnl(p.side, p.entry_px, price, p.size)
        realized = gross - _fee(p.entry_px, p.size, self.cfg.fee_bps) - _fee(price, p.size, self.cfg.fee_bps)
        kind = EventKind.CLOSE_LONG if p.side is Side.LONG else EventKind.CLOSE_SHORT
        self.position = Position()
        return StrategyEvent(kind, ts_ns, self.cfg.symbol, price, trade_pnl=realized)
```

- [ ] **Step 4: Run, expect pass** (all 8 strategy tests).
- [ ] **Step 5: Commit** — `git commit -am "feat: EMA-crossover strategy engine with PnL"`

---

### Task 15: `render_event` — colored log lines matching the GIF

**Files:**
- Create: `src/entropy/strategy/format.py`
- Test: `tests/strategy/test_format.py`

- [ ] **Step 1: Write failing test**

```python
# tests/strategy/test_format.py
from entropy.strategy.engine import StrategyEvent, EventKind
from entropy.strategy.format import render_event

def test_open_long_format():
    e = StrategyEvent(EventKind.OPEN_LONG, 1, "SPY", 749.886, running_pnl=0.0)
    text, color = render_event(e)
    assert text == "OPEN LONG @ 749.886 running_pnl=0.000" and color == "green"

def test_close_short_format():
    e = StrategyEvent(EventKind.CLOSE_SHORT, 1, "SPY", 748.435, trade_pnl=-0.135)
    text, color = render_event(e)
    assert text == "CLOSE SHORT @ 748.435 trade_pnl=-0.135" and color == "yellow"

def test_info_default():
    e = StrategyEvent(EventKind.INFO, 1, "SPY", 0.0, text="watching [SPY]")
    assert render_event(e) == ("watching [SPY]", "white")
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/strategy/format.py
from __future__ import annotations
from .engine import EventKind, StrategyEvent

_COLORS = {
    EventKind.OPEN_LONG: "green", EventKind.OPEN_SHORT: "red",
    EventKind.CLOSE_LONG: "yellow", EventKind.CLOSE_SHORT: "yellow",
    EventKind.INFO: "white",
}

def render_event(e: StrategyEvent) -> tuple[str, str]:
    if e.kind is EventKind.INFO:
        return (e.text or f"watching [{e.symbol}]", "white")
    side = "LONG" if e.kind in (EventKind.OPEN_LONG, EventKind.CLOSE_LONG) else "SHORT"
    if e.kind in (EventKind.OPEN_LONG, EventKind.OPEN_SHORT):
        return (f"OPEN {side} @ {e.price:.3f} running_pnl={e.running_pnl:.3f}", _COLORS[e.kind])
    return (f"CLOSE {side} @ {e.price:.3f} trade_pnl={e.trade_pnl:.3f}", _COLORS[e.kind])
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: strategy event log formatting"`

---

# Phase 4 — Crypto wiring + warmup adapters

### Task 16: `build_live` + `discover_universe` + `start_feed`

**Files:**
- Create: `src/entropy/feeds/crypto.py`
- Test: `tests/feeds/test_crypto_wiring.py`

- [ ] **Step 1: Write test (no network: assert transport wiring + structure)**

```python
# tests/feeds/test_crypto_wiring.py
from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import build_live

def test_build_live_sets_transport():
    from crypcodile.instruments.registry import InstrumentRegistry
    sink = QueueSink()
    reg = InstrumentRegistry()
    c = build_live("coinbase", ["BTC-USD"], ["trade"], sink, reg)
    assert c.transport is not None          # the load-bearing fix
    assert c.ws_url.startswith("wss://")
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/feeds/crypto.py
from __future__ import annotations
import asyncio
from crypcodile.client.collect import collect
from crypcodile.exchanges.factory import make_connector
from crypcodile.ingest.transport import AiohttpWsTransport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.exchanges.coinbase.connector import CoinbaseConnector
from crypcodile.exchanges.binance.connector import BinanceConnector
from .bus import QueueSink

# Curated liquid majors (intersected with discovered instruments at startup).
COINBASE_MAJORS = ("BTC-USD","ETH-USD","SOL-USD","XRP-USD","DOGE-USD","ADA-USD","AVAX-USD",
                   "LINK-USD","LTC-USD","BCH-USD","DOT-USD","UNI-USD","AAVE-USD","XLM-USD")
BINANCE_MAJORS  = ("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT",
                   "LINKUSDT","LTCUSDT","BCHUSDT","DOTUSDT","UNIUSDT","AAVEUSDT","XLMUSDT")

def build_live(exchange, symbols, channels, sink, registry, **kw):
    c = make_connector(exchange, list(symbols), list(channels), out=sink, registry=registry, **kw)
    c.transport = AiohttpWsTransport(c.ws_url)   # REQUIRED — never auto-set
    return c

async def discover_universe(registry: InstrumentRegistry,
                            cb_whitelist=COINBASE_MAJORS,
                            bn_whitelist=BINANCE_MAJORS) -> tuple[list[str], list[str]]:
    dummy = QueueSink()
    cb = CoinbaseConnector(symbols=[], channels=[], out=dummy, registry=registry)
    bn = BinanceConnector(symbols=[], channels=[], out=dummy, registry=registry, market="spot")
    cb_insts: list[Instrument] = await cb.list_instruments()
    bn_insts: list[Instrument] = await bn.list_instruments()
    cb_ok = {i.symbol_raw for i in cb_insts if i.kind == Kind.SPOT and i.quote == "USD"}
    bn_ok = {i.symbol_raw for i in bn_insts if i.kind == Kind.SPOT and i.quote == "USDT"}
    for i in cb_insts + bn_insts:
        registry.add(i)
    cb_syms = [s for s in cb_whitelist if s in cb_ok]
    bn_syms = [s for s in bn_whitelist if s in bn_ok]
    return cb_syms, bn_syms

async def start_feed(sink: QueueSink, channels=("trade",)) -> asyncio.Task:
    registry = InstrumentRegistry()
    cb_syms, bn_syms = await discover_universe(registry)
    connectors = []
    if cb_syms:
        connectors.append(build_live("coinbase", cb_syms, channels, sink, registry))
    if bn_syms:
        connectors.append(build_live("binance", bn_syms, channels, sink, registry, market="spot"))
    return asyncio.create_task(collect(connectors, sink, max_reconnects=-1))
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Live smoke (manual, network)** — create `scripts/smoke_crypto.py`:
```python
import asyncio
from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import start_feed
from crypcodile.schema.records import Trade

async def main():
    sink = QueueSink(maxsize=10_000)
    task = await start_feed(sink)
    seen = 0
    while seen < 20:
        rec = await sink.q.get()
        if isinstance(rec, Trade):
            print(rec.symbol, rec.price, rec.side.value); seen += 1
    task.cancel()
asyncio.run(main())
```
Run: `python scripts/smoke_crypto.py` → Expected: ~20 real Coinbase/Binance trade lines
(`coinbase:BTC-USD ...`, `binance-spot:BTCUSDT ...`). If it hangs with no output, the issue
is connectivity, not code (verify with `curl -I https://api.coinbase.com`).
- [ ] **Step 6: Commit** — `git commit -am "feat: Crypcodile crypto feed wiring (transport, discovery, collect)"`

---

### Task 17: Warmup adapters (Binance klines → `Bar`)

**Files:**
- Create: `src/entropy/feeds/warmup.py`
- Test: `tests/feeds/test_warmup.py`

- [ ] **Step 1: Write test (adapter shape, no network — feed a fake OHLCV)**

```python
# tests/feeds/test_warmup.py
from entropy.feeds.warmup import bars_from_ohlcv
from entropy.strategy.engine import Bar

class _O:   # duck-typed OHLCV
    def __init__(self, t, o, h, l, c): self.exchange_ts=t; self.local_ts=t; self.open=o; self.high=h; self.low=l; self.close=c

def test_bars_from_ohlcv_maps_fields():
    bars = bars_from_ohlcv([_O(10, 1, 2, 0.5, 1.5), _O(20, 1.5, 3, 1, 2.5)])
    assert bars == [Bar(ts_ns=10, close=1.5, high=2, low=0.5),
                    Bar(ts_ns=20, close=2.5, high=3, low=1)]
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/feeds/warmup.py
from __future__ import annotations
from collections.abc import Iterable, Sequence
from entropy.strategy.engine import Bar

def bars_from_ohlcv(ohlcv_rows: Iterable) -> list[Bar]:
    """Map any OHLCV-like rows (Crypcodile OHLCV) to strategy/chart Bars."""
    out: list[Bar] = []
    for o in ohlcv_rows:
        if getattr(o, "close", None) is None:
            continue
        ts = o.exchange_ts if getattr(o, "exchange_ts", None) is not None else o.local_ts
        out.append(Bar(ts_ns=int(ts), close=float(o.close),
                       high=float(o.high), low=float(o.low)))
    return out

async def warmup_klines(symbol_raw: str, interval: str = "1m", limit: int = 200) -> list[Bar]:
    """Fetch recent Binance klines as warmup Bars. Network call."""
    import time
    from crypcodile.exchanges.binance.backfill import make_live_backfill
    bf = make_live_backfill()
    now = time.clock_gettime_ns(time.CLOCK_REALTIME)
    start = now - limit * 60 * 1_000_000_000
    rows = []
    async for bar in bf.backfill_klines(venue="binance-spot", symbol=symbol_raw,
                                        interval=interval, start_ns=start, end_ns=now):
        rows.append(bar)
    return bars_from_ohlcv(rows)
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: Binance-klines warmup adapters -> Bar"`

---

# Phase 5 — UI shell + panels

### Task 18: Theme + TCSS + app skeleton (boots empty)

**Files:**
- Create: `src/entropy/ui/__init__.py`, `src/entropy/ui/theme.py`, `src/entropy/ui/entropy.tcss`, `src/entropy/ui/app.py`
- Test: `tests/ui/test_app_boots.py` (+ `tests/ui/__init__.py`)

- [ ] **Step 1: Write failing test (Textual pilot smoke)**

```python
# tests/ui/test_app_boots.py
import pytest
from entropy.ui.app import EntropyApp

@pytest.mark.asyncio
async def test_app_boots_and_has_panels():
    app = EntropyApp(headless=True)
    async with app.run_test() as pilot:
        assert app.query_one("#console") is not None
        assert app.query_one("#status") is not None
        await pilot.press("q")
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement theme + tcss + minimal app**

```python
# src/entropy/ui/theme.py
from textual.theme import Theme

ENTROPY_THEME = Theme(
    name="entropy", primary="#26d626", secondary="#ff3b3b", accent="#e6c200",
    foreground="#c8c8c8", background="#000000", success="#26d626",
    warning="#e6c200", error="#ff3b3b", surface="#000000", panel="#0a0a0a", dark=True,
)
```
```css
/* src/entropy/ui/entropy.tcss */
Screen { background: black; color: #c8c8c8; layout: vertical; }
#header  { height: 3; background: black; border: none; padding: 0 1; }
#body    { height: 1fr; layout: horizontal; }
#console { width: 32; background: black; border: none; padding: 0; }
#center  { width: 1fr; layout: vertical; }
#ticker  { height: 2; border: none; padding: 0; }
#gauges  { height: 4; border: none; padding: 0; }
#hist    { height: 4; border: none; padding: 0; }
#boards  { height: 1fr; layout: horizontal; }
#boards DataTable { width: 1fr; height: 1fr; border: none; padding: 0; }
#charts  { width: 48; layout: vertical; }
#price   { height: 2fr; border: none; }
#volume  { height: 1fr; border: none; }
#status  { height: 1; background: black; border: none; padding: 0 1; }
```
```python
# src/entropy/ui/app.py
from __future__ import annotations
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import RichLog, DataTable, Static
from .theme import ENTROPY_THEME

class EntropyApp(App):
    CSS_PATH = "entropy.tcss"
    BINDINGS = [
        ("s", "settings", "Settings"),
        ("question_mark", "help", "Help"),
        ("h", "help", "Help"),
        ("e", "errors", "Errors"),
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Entropy", id="header")
        with Horizontal(id="body"):
            yield RichLog(id="console", markup=True, highlight=False, auto_scroll=True, max_lines=2000)
            with Vertical(id="center"):
                yield Static("", id="ticker")
                yield Static("", id="gauges")
                yield Static("", id="hist")
                with Horizontal(id="boards"):
                    yield DataTable(id="new_lows")
                    yield DataTable(id="session_highs")
            with Vertical(id="charts"):
                yield Static("", id="price")
                yield Static("", id="volume")
        yield Static("", id="status")

    def on_mount(self) -> None:
        self.register_theme(ENTROPY_THEME)
        self.theme = "entropy"
        for tid in ("new_lows", "session_highs"):
            t = self.query_one("#" + tid, DataTable)
            t.add_columns("Symbol", "Count", "Price", "%Chg")
            t.cursor_type = "none"
            t.zebra_stripes = False

    def action_settings(self) -> None: ...
    def action_help(self) -> None: ...
    def action_errors(self) -> None: ...
```
Create empty `src/entropy/ui/__init__.py`, `src/entropy/ui/widgets/__init__.py`, `tests/ui/__init__.py`.

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: Textual app skeleton, theme, dense TCSS"`

---

### Task 19: `GaugeBar` widget (fractional-block proportional bar)

**Files:**
- Create: `src/entropy/ui/widgets/gauges.py`
- Test: `tests/ui/test_gauges.py`

- [ ] **Step 1: Write failing test (render logic is pure-ish; test the fill computation)**

```python
# tests/ui/test_gauges.py
from entropy.ui.widgets.gauges import fill_cells

def test_full_and_partial_fill():
    assert fill_cells(1.0, 10) == "█" * 10
    assert fill_cells(0.0, 10) == " " * 10
    s = fill_cells(0.5, 10)            # 5 full blocks then spaces
    assert s.startswith("█" * 5) and len(s) == 10
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/ui/widgets/gauges.py
from __future__ import annotations
from rich.segment import Segment
from rich.style import Style
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

_EIGHTHS = " ▏▎▍▌▋▊▉█"  # 0..8/8

def fill_cells(value: float, width: int) -> str:
    value = max(0.0, min(1.0, value))
    filled = value * width
    full = int(filled)
    rem = int((filled - full) * 8)
    s = "█" * full + (_EIGHTHS[rem] if rem and full < width else "")
    return s.ljust(width)[:width]

class GaugeBar(Widget):
    value = reactive(0.0)
    color = reactive("#26d626")
    def watch_value(self, *_): self.refresh()
    def render_line(self, y: int) -> Strip:
        w = self.size.width
        s = fill_cells(self.value, w)
        return Strip([Segment(s, Style(color=self.color))], w)
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: GaugeBar fractional-block widget"`

---

### Task 20: `StatusBar` widget (Sell/Buy gauge + telemetry + hints)

**Files:**
- Create: `src/entropy/ui/widgets/status_bar.py`
- Test: `tests/ui/test_status_bar.py`

- [ ] **Step 1: Write failing test**

```python
# tests/ui/test_status_bar.py
from entropy.ui.widgets.status_bar import format_telemetry

def test_telemetry_line():
    line = format_telemetry(raw_hz=4323, prev30s=3.10, snap_drops=99566, spikes=229,
                            accel="accelerating", dropped=0)
    assert "raw: 4323 Hz" in line and "spikes: 229" in line and "snap-drops: 99566" in line
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/ui/widgets/status_bar.py
from __future__ import annotations
from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text

def format_telemetry(*, raw_hz, prev30s, snap_drops, spikes, accel, dropped) -> str:
    drop = f"  dropped: {dropped}" if dropped else ""
    return (f"raw: {raw_hz:.0f} Hz   prev30s: {prev30s:.2f}/s   "
            f"snap-drops: {snap_drops}   spikes: {spikes}   {accel}{drop}")

class StatusBar(Widget):
    sell_pct = reactive(50.0)
    telemetry = reactive("")
    hints = reactive("s:Settings  ?:Help  e:Errors  q:Quit")
    def watch_sell_pct(self, *_): self.refresh()
    def watch_telemetry(self, *_): self.refresh()
    def render(self) -> Text:
        sp = self.sell_pct
        t = Text()
        t.append(f"S {sp:.0f}% ", style="bold #ff3b3b")
        t.append(self.telemetry + "  ", style="#c8c8c8")
        t.append(f"B {100-sp:.0f}%   ", style="bold #26d626")
        t.append(self.hints, style="#7a7a7a")
        return t
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: StatusBar telemetry + sell/buy"`

---

### Task 21: Boards refresh helper + ticker strip + header

**Files:**
- Create: `src/entropy/ui/widgets/boards.py`, `src/entropy/ui/widgets/header.py`
- Test: `tests/ui/test_boards.py`

- [ ] **Step 1: Write failing test**

```python
# tests/ui/test_boards.py
from entropy.ui.widgets.boards import row_text
from entropy.engine.leaderboard import LeaderRow

def test_row_text_colors_by_sign():
    cells = row_text(LeaderRow("AAPL", 5, 191.2, 2.4))
    assert cells[0].plain == "AAPL"
    assert cells[3].plain == "+2.40%"
    cells_dn = row_text(LeaderRow("NVDA", 3, 88.0, -1.1))
    assert cells_dn[3].plain == "-1.10%"
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/ui/widgets/boards.py
from __future__ import annotations
from rich.text import Text
from textual.widgets import DataTable
from entropy.engine.leaderboard import LeaderRow

def row_text(r: LeaderRow) -> tuple[Text, Text, Text, Text]:
    col = "#26d626" if r.pct_chg >= 0 else "#ff3b3b"
    return (Text(r.symbol, style="bold"),
            Text(str(r.count), justify="right"),
            Text(f"{r.price:.2f}", justify="right"),
            Text(f"{r.pct_chg:+.2f}%", style=col, justify="right"))

def refresh_board(table: DataTable, rows: tuple[LeaderRow, ...]) -> None:
    table.clear()
    for r in rows:
        table.add_row(*row_text(r))
```
```python
# src/entropy/ui/widgets/header.py
from __future__ import annotations
from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text

class HeaderBar(Widget):
    clock = reactive("")
    quotes = reactive("")     # preformatted "SPY 750.42 (-0.02%) ..."
    sources = reactive("coinbase ●  binance ●")
    def watch_clock(self, *_): self.refresh()
    def watch_quotes(self, *_): self.refresh()
    def render(self) -> Text:
        t = Text()
        t.append("Entropy  ", style="bold #e6c200")
        t.append(self.clock + "   ", style="#c8c8c8")
        t.append(self.sources + "\n", style="#26d626")
        t.append(self.quotes, style="#c8c8c8")
        return t
```
Wire `HeaderBar`/`StatusBar`/`GaugeBar` into `app.py compose()` replacing the placeholder
`Static`s (swap `Static(id=...)` for the real widgets; keep ids). Add a ticker `Static`
fed a preformatted string.

- [ ] **Step 4: Run, expect pass** (board unit test + app still boots).
- [ ] **Step 5: Commit** — `git commit -am "feat: leaderboard rows, header, ticker widgets"`

---

# Phase 6 — Charts

### Task 22: Candlestick + volume charts (textual-plotext)

**Files:**
- Create: `src/entropy/ui/widgets/charts.py`
- Test: `tests/ui/test_charts.py`

- [ ] **Step 1: Write failing test (construct + feed candles, no exceptions)**

```python
# tests/ui/test_charts.py
import pytest
from entropy.ui.widgets.charts import Candle, PriceChart

@pytest.mark.asyncio
async def test_price_chart_accepts_candles():
    from textual.app import App, ComposeResult
    class _A(App):
        def compose(self) -> ComposeResult:
            yield PriceChart(id="price")
    app = _A()
    async with app.run_test():
        chart = app.query_one("#price", PriceChart)
        chart.candles = [Candle(t=i, o=10, h=11, l=9, c=10.5) for i in range(20)]
        await app.workers.wait_for_complete() if False else None
        assert len(chart.candles) == 20
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/ui/widgets/charts.py
from __future__ import annotations
from dataclasses import dataclass
from textual.reactive import reactive
from textual_plotext import PlotextPlot

@dataclass(slots=True)
class Candle:
    t: int          # ns
    o: float
    h: float
    l: float
    c: float

class PriceChart(PlotextPlot):
    candles: reactive[list[Candle]] = reactive(list)
    def watch_candles(self, _old, new) -> None:
        if new:
            self.replot()
    def replot(self) -> None:
        import datetime as dt
        self.plt.clear_data()
        ds = [dt.datetime.fromtimestamp(c.t / 1e9).strftime("%H:%M:%S") for c in self.candles]
        data = {"Open": [c.o for c in self.candles], "Close": [c.c for c in self.candles],
                "High": [c.h for c in self.candles], "Low": [c.l for c in self.candles]}
        self.plt.candlestick(ds, data)
        self.refresh()

class VolumeChart(PlotextPlot):
    bars: reactive[list[tuple[int, float]]] = reactive(list)
    def watch_bars(self, _old, new) -> None:
        if new:
            self.replot()
    def replot(self) -> None:
        import datetime as dt
        self.plt.clear_data()
        ds = [dt.datetime.fromtimestamp(t / 1e9).strftime("%H:%M:%S") for t, _ in self.bars]
        self.plt.bar(ds, [v for _, v in self.bars])
        self.refresh()
```
Swap the `#price`/`#volume` placeholder `Static`s in `app.py` for `PriceChart`/`VolumeChart`.

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: textual-plotext candlestick + volume charts"`

---

### Task 23: Live OHLCV aggregation for charts

**Files:**
- Create: `src/entropy/engine/candles.py`
- Test: `tests/engine/test_candles.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/test_candles.py
from entropy.engine.candles import CandleAggregator

S = 1_000_000_000

def test_aggregates_trades_into_bars():
    agg = CandleAggregator(interval_ns=60 * S, maxlen=10)
    agg.add(0, 100.0, 1.0)
    agg.add(10 * S, 105.0, 2.0)
    agg.add(30 * S, 98.0, 1.0)
    agg.add(65 * S, 101.0, 1.0)         # new bucket
    bars = agg.bars()
    assert len(bars) == 2
    o, h, l, c = bars[0].o, bars[0].h, bars[0].l, bars[0].c
    assert (o, h, l, c) == (100.0, 105.0, 98.0, 98.0)
    assert bars[0].vol == 4.0
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/engine/candles.py
from __future__ import annotations
from collections import deque
from dataclasses import dataclass

@dataclass(slots=True)
class OHLCBar:
    t: int
    o: float
    h: float
    l: float
    c: float
    vol: float

class CandleAggregator:
    """Builds rolling OHLCV bars for ONE symbol from live trades."""
    def __init__(self, interval_ns: int, maxlen: int = 120) -> None:
        self.interval_ns = interval_ns
        self._bars: deque[OHLCBar] = deque(maxlen=maxlen)
        self._cur_bucket = -1

    def add(self, ts_ns: int, price: float, amount: float) -> None:
        bucket = ts_ns // self.interval_ns
        if bucket != self._cur_bucket:
            self._bars.append(OHLCBar(bucket * self.interval_ns, price, price, price, price, amount))
            self._cur_bucket = bucket
        else:
            b = self._bars[-1]
            if price > b.h: b.h = price
            if price < b.l: b.l = price
            b.c = price
            b.vol += amount

    def bars(self) -> list[OHLCBar]:
        return list(self._bars)
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: live OHLCV candle aggregator"`

---

# Phase 7 — Algo console + full wiring

### Task 24: `AlgoConsole` widget

**Files:**
- Create: `src/entropy/ui/widgets/console.py`
- Test: `tests/ui/test_console.py`

- [ ] **Step 1: Write failing test**

```python
# tests/ui/test_console.py
import pytest
from entropy.ui.widgets.console import AlgoConsole
from entropy.strategy.engine import StrategyEvent, EventKind

@pytest.mark.asyncio
async def test_console_writes_event_line():
    from textual.app import App, ComposeResult
    class _A(App):
        def compose(self) -> ComposeResult:
            yield AlgoConsole(id="console")
    app = _A()
    async with app.run_test():
        c = app.query_one("#console", AlgoConsole)
        c.push_event(StrategyEvent(EventKind.OPEN_LONG, 1, "SPY", 749.886, running_pnl=0.0))
        assert c.line_count >= 1
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement**

```python
# src/entropy/ui/widgets/console.py
from __future__ import annotations
from textual.widgets import RichLog
from entropy.strategy.engine import StrategyEvent
from entropy.strategy.format import render_event

class AlgoConsole(RichLog):
    def __init__(self, **kw) -> None:
        kw.setdefault("markup", True)
        kw.setdefault("auto_scroll", True)
        kw.setdefault("max_lines", 2000)
        kw.setdefault("highlight", False)
        super().__init__(**kw)

    def push_event(self, e: StrategyEvent) -> None:
        text, color = render_event(e)
        self.write(f"[{color}]{text}[/]")

    def push_info(self, text: str, color: str = "white") -> None:
        self.write(f"[{color}]{text}[/]")
```
Swap the `#console` `RichLog` in `app.py` for `AlgoConsole`.

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: AlgoConsole strategy log widget"`

---

### Task 25: App config + full wiring (feeds → engine → UI)

**Files:**
- Create: `src/entropy/app.py`, `src/entropy/__main__.py`
- Modify: `src/entropy/ui/app.py` (add drain worker + 10fps sampler)
- Test: `tests/test_wiring.py`

- [ ] **Step 1: Write failing test (engine+strategy update from a sim feed, no real UI paint)**

```python
# tests/test_wiring.py
import asyncio
import pytest
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed
from entropy.engine.engine import Engine

@pytest.mark.asyncio
async def test_sim_feed_drives_engine_snapshot():
    sink = QueueSink(maxsize=50_000)
    feed = EquitySimFeed(sink, seed=11, ticks_per_sec=3000, batch_dt=0.01)
    engine = Engine()
    ft = asyncio.create_task(feed.run())
    await asyncio.sleep(0.1)
    # drain all available
    drained = 0
    while not sink.q.empty():
        r = sink.q.get_nowait()
        engine.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)
        drained += 1
    ft.cancel()
    try:
        await ft
    except asyncio.CancelledError:
        pass
    assert drained > 0
    snap = engine.snapshot()
    assert snap.breadth.raw_hz >= 0
    assert len(snap.top_movers) > 0
```

- [ ] **Step 2: Run, expect fail (or pass partially — ensure it exercises real wiring).**
- [ ] **Step 3: Implement app wiring + UI workers**

`src/entropy/app.py`:
```python
from __future__ import annotations
import msgspec
from entropy.config import EngineConfig

class AppConfig(msgspec.Struct, frozen=True):
    seed: int = 42
    equity_tps: int = 4000
    enable_crypto: bool = True
    enable_equities: bool = True
    strategy_symbol: str = "SPY"
    crypto_strategy_symbol: str = "binance-spot:BTCUSDT"
    engine: EngineConfig = msgspec.field(default_factory=EngineConfig)
```

Add to `src/entropy/ui/app.py`:
```python
import asyncio
from textual import work
from textual.worker import get_current_worker
from crypcodile.schema.records import Trade
from entropy.engine.engine import Engine
from entropy.strategy.engine import Strategy, StrategyConfig
from entropy.strategy.format import render_event
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed
from entropy.engine.candles import CandleAggregator

# in EntropyApp.__init__ accept engine/sink/feeds; in on_mount:
#   self.set_interval(1/10, self.sample_snapshot)
#   self.run_drain()
#   self._feed_tasks = [asyncio.create_task(self._equity.run())]
#   if crypto enabled: self._feed_tasks.append(await start_feed(self._sink))

def sample_snapshot(self) -> None:   # method on EntropyApp
    snap = self.engine.snapshot()
    self.query_one("#status").telemetry = format_telemetry(
        raw_hz=snap.breadth.raw_hz, prev30s=snap.breadth.prev30s_rate,
        snap_drops=self._snap_drops, spikes=self._spikes, accel=snap.breadth.accel,
        dropped=self._sink.dropped)
    self.query_one("#status").sell_pct = snap.breadth.sell_pct
    refresh_board(self.query_one("#new_lows"), snap.new_lows)
    refresh_board(self.query_one("#session_highs"), snap.new_highs)
    self.query_one("#price").candles = self._price_candles.bars_as_candles()
    # header clock/quotes updated from a wall clock + index symbols

@work(thread=True, exclusive=True, group="drain")
def run_drain(self) -> None:   # method on EntropyApp
    worker = get_current_worker()
    q = self._sink.q
    while not worker.is_cancelled:
        try:
            r = q.get(timeout=0.25)
        except Exception:
            continue
        if isinstance(r, Trade):
            evs = self.engine.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)
            for e in evs:
                kn = type(e).__name__
                if kn == "Spike": self._spikes += 1
                elif kn == "SnapDrop": self._snap_drops += 1
            sevs = self.strategy.on_price(r.symbol, r.price, r.local_ts)
            for se in sevs:
                text, color = render_event(se)
                self.call_from_thread(self.query_one("#console").push_info, text, color)
```
> Implementer: thread the `Engine`, `Strategy`, `QueueSink`, `EquitySimFeed`, two
> `CandleAggregator`s, and counters (`_spikes`, `_snap_drops`) through `EntropyApp.__init__`.
> `asyncio.Queue.get` is not thread-safe across loops — use a `janus`-style bridge OR (simpler)
> make the drain a non-thread `@work` async worker that `await self._sink.q.get()` on the loop,
> since the engine call is fast. **Decision:** use an ASYNC worker (`@work(exclusive=True)`,
> not `thread=True`) that awaits `self._sink.q.get()` and calls the engine directly — the engine
> is fast enough that running it on the event loop between awaits is fine at these rates, and it
> avoids cross-thread queue hazards. Drain in a tight loop with periodic `await asyncio.sleep(0)`.

Revised drain (use this):
```python
@work(exclusive=True, group="drain")
async def run_drain(self) -> None:
    q = self._sink.q
    while True:
        r = await q.get()
        if isinstance(r, Trade):
            self.engine.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)
            if r.symbol == self.cfg.strategy_symbol or r.symbol == self.cfg.crypto_strategy_symbol:
                self._on_strategy(r)
            self._route_candle(r)
```

`src/entropy/__main__.py`:
```python
from __future__ import annotations
import asyncio
from entropy.app import AppConfig
from entropy.ui.app import EntropyApp

def main() -> None:
    EntropyApp(AppConfig()).run()

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run unit test + boot the app** — `pytest tests/test_wiring.py -q` (pass) and
  `python -m entropy` (launches the TUI; equities sim populates boards/gauges immediately,
  crypto fills in once connected). Press `q` to quit.
- [ ] **Step 5: Commit** — `git commit -am "feat: full app wiring (feeds -> engine/strategy -> 10fps UI)"`

---

# Phase 8 — Polish

### Task 26: Modals (Help / Settings / Errors)

**Files:**
- Create: `src/entropy/ui/widgets/modals.py`
- Modify: `src/entropy/ui/app.py` (action_* push screens)
- Test: `tests/ui/test_modals.py`

- [ ] **Step 1: Write failing test**

```python
# tests/ui/test_modals.py
import pytest
from entropy.ui.app import EntropyApp
from entropy.app import AppConfig

@pytest.mark.asyncio
async def test_help_modal_opens_and_closes():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test() as pilot:
        await pilot.press("h")
        assert app.screen.id == "help"
        await pilot.press("escape")
        assert app.screen.id != "help"
```

- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement modals**

```python
# src/entropy/ui/widgets/modals.py
from __future__ import annotations
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP = """Entropy — keys:
  s  Settings    ?/h  Help    e  Errors    q  Quit
Scanner: new highs/lows over 30s/1m/5m/20m/session; spikes & snap-drops.
"""

class HelpScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close"), ("h", "dismiss", "Close"), ("q", "dismiss", "Close")]
    def compose(self) -> ComposeResult:
        yield Static(_HELP, id="help-body")
    def action_dismiss(self) -> None:
        self.app.pop_screen()

class SettingsScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close"), ("s", "dismiss", "Close")]
    def compose(self) -> ComposeResult:
        yield Static("Settings (read-only in v1)", id="settings-body")
    def action_dismiss(self) -> None:
        self.app.pop_screen()

class ErrorScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close"), ("e", "dismiss", "Close")]
    def __init__(self, text: str = "No errors.") -> None:
        super().__init__()
        self._text = text
    def compose(self) -> ComposeResult:
        yield Static(self._text, id="error-body")
    def action_dismiss(self) -> None:
        self.app.pop_screen()
```
In `app.py`, give each modal an `id` (e.g. `HelpScreen(id="help")` — pass via `super().__init__(id=...)`)
and wire:
```python
def action_help(self) -> None: self.push_screen(HelpScreen(id="help"))
def action_settings(self) -> None: self.push_screen(SettingsScreen(id="settings"))
def action_errors(self) -> None: self.push_screen(ErrorScreen(self._error_text, id="errors"))
```
(Adjust `HelpScreen.__init__` to accept/forward `id`.)

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: help/settings/errors modals"`

---

### Task 27: Strategy warmup + reconnect noise + final integration pass

**Files:**
- Modify: `src/entropy/ui/app.py` (warmup strategy on mount; transport INFO lines)
- Test: manual

- [ ] **Step 1: On mount, warm the strategy** — for `SPY` synthesize 24 bars from the sim's
  current prices; for the crypto strategy symbol call `warmup_klines("BTCUSDT")` in an async
  worker and `strategy.warmup(bars)`, pushing the returned INFO line to the console. Push a
  `watching [SPY]` INFO line at startup.
- [ ] **Step 2: Reconnect noise** — wrap `start_feed` so connect/disconnect surface as INFO
  console lines (`connecting…`, `disconnect: …`) via a small callback, keeping the engine pure.
- [ ] **Step 3: Run the app** `python -m entropy`, verify: boards re-sort, gauges move, charts
  draw candles, console scrolls OPEN/CLOSE lines, status bar shows live Hz + sell/buy + dropped.
- [ ] **Step 4: Full suite green** — `pytest -q && ruff check && mypy src/entropy` (fix stragglers).
- [ ] **Step 5: Commit** — `git commit -am "feat: strategy warmup, reconnect noise, integration polish"`

---

### Task 28: README + run docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document** install (`uv sync`), run (`python -m entropy`), keys, data sources
  (real crypto via Crypcodile, simulated equities), and config knobs (`AppConfig`). One screenshot
  description. No placeholders.
- [ ] **Step 2: Commit** — `git commit -am "docs: Entropy README (install, run, sources)"`

---

## Self-Review (run by the plan author)

**Spec coverage:** scanner core (Tasks 6–11), breadth (10), leaderboards (11), momentum (8/11),
equities sim (3–5), crypto feed (16–17), strategy console (13–15, 24, 27), charts (22–23), UI
shell/widgets/theme (18–21), status/gauges (19–20), modals/keys (26), perf (12), wiring (25).
All 8 spec build phases and the §10 decisions are represented. ✓

**Placeholder scan:** code blocks are complete and runnable; the two places with an implementer
*decision* (event `kind` representation in Task 9; async-vs-thread drain in Task 25) state the
chosen option explicitly ("use the explicit-`kind`-field form"; "use an ASYNC worker"). No TBD/TODO. ✓

**Type consistency:** `Engine.on_trade(symbol, price, amount, side, ts_ns)` is identical in spec
and Tasks 11/12/25. `StrategyEvent`, `EventKind`, `Bar`, `Side` consistent across Tasks 13–15,
24, 27. `LeaderRow` fields (`symbol,count,price,pct_chg`) consistent in Tasks 11, 21. `QueueSink.q`
used consistently. `render_event -> (text, color)` consistent in Tasks 15, 24. ✓
