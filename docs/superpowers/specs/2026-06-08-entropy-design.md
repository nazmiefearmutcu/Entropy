# Entropy — Design Specification

**Date:** 2026-06-08
**Status:** Approved (design), pre-implementation
**Author:** nazmiefearmutcu

A real-time, terminal-based (TUI) market-scanner and algo-trading console, built in
Python 3.12 + asyncio + Textual. It is a complete, from-scratch reconstruction of a
tool seen in a reference recording ("HighLowTicker"): a dense Bloomberg-style terminal
that streams live market data, detects new highs/lows and momentum across rolling
windows, ranks movers, runs a demo scalping strategy, and renders live candlestick
charts — all in colored monospace text.

The entire application is in **English**. Crypto data is **real**, sourced from the
existing **Crypcodile** engine (`/Users/nazmi/Crypcodile`). US equities are **simulated**
through the same normalized record path so the engine treats both markets uniformly.

---

## 1. Goals & Non-Goals

### Goals
1. Faithfully reproduce the original's *interface and working logic* as a new, clean codebase.
2. Real live crypto feed via Crypcodile (Coinbase + Binance spot websockets).
3. Simulated equities (SPY/QQQ/IWM + ~180 real tickers) on the identical record path.
4. All five original components: scanner core, breadth gauges, leaderboards, algo console, live charts.
5. A pure, unit-tested detection engine that stays correct and fast at kHz tick rates.
6. Dense, raw terminal aesthetic (black bg, green/red/yellow, no borders).

### Non-Goals (v1)
- No real order execution (the strategy console is a demo; no broker, no real trades).
- No persistence/replay lake (DuckDB/Parquet) — Entropy is **ephemeral, live-only** in v1.
- No equities *real* feed (simulated only; the feed interface allows swapping later).
- No cross-venue symbol unification (Coinbase `BTC-USD` and Binance `BTCUSDT` are distinct rows in v1).
- No web/desktop client — TUI only.

---

## 2. Reference Analysis (the original)

The recording shows a four-column terminal:

| Region | Content | Function |
|---|---|---|
| Left console | `OPEN/CLOSE LONG/SHORT @ px running_pnl/trade_pnl`, `watching [SPY]`, `Yahoo warmup: 24 bars, EMA…`, ws reconnect noise | Live algo scalping log on SPY |
| Header | `HighLowTicker`, clock, `tradier ● coinbase` pills, `SPY/QQQ/IWM` quotes | Title + source status + index quotes |
| Center | `30s/1m/5m` grouped symbol counts; dual Lows/Highs bar gauges; `rate` histogram; `On new lows`/`Session new highs` tables | Multi-window high/low detection + leaderboards |
| Right | Two stacked candlestick charts (SPY + a crypto), price + volume, live price tags | Live charts |
| Status bar | `S 72% ▕███▏██▎ B 28%`, `raw: 4323 Hz · prev30s: 3.10/s · snap-drops: 99566 · spikes: 229 · ● Accelerating`, `s:Settings ?:Help e:Errors q:Quit` | Breadth + event telemetry + hints |

**Working logic distilled:** a backend streamed many symbols; per symbol, rolling-window
max/min produced *new high/low* events over 30s/1m/5m/20m/session; momentum thresholds
produced *spikes* and *snap-drops*; aggregates produced breadth (Sell%/Buy%, highs vs
lows, event Hz); a separate scalping algo traded SPY; charts rendered live OHLCV.

---

## 3. Architecture

### 3.1 Process & concurrency model
**Single process, single asyncio event loop, in-process queue** (no localhost websocket
relay — that was the original's infra choice; we do better). Crypcodile is `pip install -e`
as a sibling dependency and imported directly.

```
┌─ asyncio event loop (one process) ───────────────────────────────────────┐
│                                                                           │
│  Crypto connectors (Crypcodile collect())  ─┐                             │
│                                              ├─→ QueueSink ─→ asyncio.Queue│
│  Equity simulator (EquitySimFeed.run())    ─┘        (bounded, drop-oldest)│
│                                                              │            │
│  Textual @work(thread=True) drain worker:  pull ALL queued  │            │
│     per frame → Engine.on_trade(...) (pure) + Strategy.on_price(...)      │
│                                                              │            │
│  Textual set_interval(1/10) sampler (event loop): Engine.snapshot()       │
│     → assign widget reactives → repaint at ~10 fps                        │
└───────────────────────────────────────────────────────────────────────────┘
```

Two cooperating loops:
- **Data drain** (`@work(thread=True, exclusive=True)`): consumes the kHz queue, calls the
  pure engine + strategy, writes algo log lines via `call_from_thread`.
- **UI sampler** (`set_interval(1/10, …)` on the event loop): reads a cheap immutable
  `Engine.snapshot()` and updates widgets. **Engine ticks at kHz; UI deliberately
  undersamples at 10 fps.**

> **Critical decoupling:** the engine never renders; the UI never iterates live kHz state.
> They share only the `Engine` object via `snapshot()` (a small frozen copy).

### 3.2 Module map
```
src/entropy/
  __init__.py
  __main__.py              # `python -m entropy` entrypoint
  app.py                   # wiring: build feeds + engine + strategy + Textual app, TaskGroup
  config.py                # AppConfig, EngineConfig, StrategyConfig, universe selection, sources
  feeds/
    bus.py                 # QueueSink(Sink) + bounded asyncio.Queue (drop-oldest)
    crypto.py              # Crypcodile wiring: discover_universe, build_live(), start_feed()
    warmup.py              # Binance-klines warmup adapters → Bar/OHLC (chart + strategy seed)
    equities/
      universe.py          # ticker lists + SymParams + build_params()
      sim.py               # EquitySimulator (pure, seeded RNG, injected clock)
      feed.py              # EquitySimFeed (async task → Sink)
  engine/
    events.py              # Event structs + enums (NewHigh/NewLow/Spike/SnapDrop/UpMove/DownMove)
    windows.py             # MonotonicExtreme, SessionExtreme, MomentumHorizon, RollingTape
    rate.py                # RateMeter (1s-bucket sliding counter)
    breadth.py             # BreadthTracker (Sell%/Buy%, Hz, accel, per-window NH/NL sets)
    leaderboard.py         # LeaderRow + heapq ranking
    engine.py              # Engine.on_trade(...) + snapshot() + EngineSnapshot
  strategy/
    ema.py                 # EmaState + ema_update (pure)
    engine.py              # Strategy, Position, StrategyEvent, Bar, StrategyConfig
    format.py              # render_event() → (text, color)
  ui/
    app.py                 # Entropy(App): compose(), workers, sampler, bindings, modals
    theme.py               # ENTROPY_THEME
    entropy.tcss           # dense/raw styling
    widgets/
      header.py            # HeaderBar (title, clock, source pills, index quotes)
      ticker_strip.py      # TickerStrip (per-window symbol counts)
      gauges.py            # GaugeBar (fractional-block), HighLowGauges, SellBuyBar
      histogram.py         # EventHistogram (rate)
      boards.py            # NewLowsBoard / SessionHighsBoard (DataTable)
      console.py           # AlgoConsole (RichLog)
      charts.py            # PriceChart/VolumeChart (textual-plotext) [+ Candles render_line fallback]
      status_bar.py        # StatusBar (Sell/Buy gauge + telemetry + hints)
      modals.py            # HelpScreen / SettingsScreen / ErrorScreen (ModalScreen)
tests/                     # mirrors src/entropy (engine + strategy + sim have full unit suites)
```

### 3.3 Engine input boundary (decoupling decision)
The engine is **dependency-free and offline-testable**. To avoid coupling it to Crypcodile
*and* avoid a second per-tick struct allocation, `Engine.on_trade` takes **primitives**:

```python
def on_trade(self, symbol: str, price: float, amount: float, side: str, ts_ns: int) -> list[Event]
```

- Crypto drain extracts: `engine.on_trade(t.symbol, t.price, t.amount, t.side.value, t.local_ts)`
- Equity sim extracts the same from its `Trade` (which it builds from `crypcodile.schema.records.Trade`).

This reconciles the "reuse Crypcodile Trade" vs "engine owns its record" tension: the engine
owns *no record type*, reads only primitives, and is trivially testable with literal values.

---

## 4. Data Layer — Crypcodile integration (verified against source)

All facts below are **verified** against `/Users/nazmi/Crypcodile` source.

### 4.1 Verified integration recipe
```python
from crypcodile.exchanges.factory import make_connector       # (exchange, symbols, channels, out, registry, **kw)
from crypcodile.ingest.transport import AiohttpWsTransport     # AiohttpWsTransport(url)
from crypcodile.client.collect import collect                  # collect(connectors, sink, *, max_reconnects=-1)
from crypcodile.instruments.registry import InstrumentRegistry, Kind
from crypcodile.sink.base import Sink                          # async put/flush; close()->flush()
from crypcodile.schema.records import Trade, BookTicker, Record
from crypcodile.schema.enums import Side                       # BUY/SELL/UNKNOWN
from crypcodile.exchanges.binance.backfill import make_live_backfill  # klines warmup
```

**Mandatory transport wiring** — the factory/connectors leave `transport=None`; `run()`
raises `RuntimeError('No transport configured')` otherwise. A single helper prevents the
omission (the shipped Crypcodile example forgets it — a latent bug we must not copy):

```python
def build_live(exchange, symbols, channels, sink, registry, **kw):
    c = make_connector(exchange, symbols, channels, out=sink, registry=registry, **kw)
    c.transport = AiohttpWsTransport(c.ws_url)   # REQUIRED
    return c
```

**Two connectors total for the whole crypto universe** (connectors multiplex many symbols
over one websocket — never one-connector-per-symbol):
- `build_live("coinbase", cb_syms, ["trade"], sink, registry)` — USD spot
- `build_live("binance", bn_syms, ["trade"], sink, registry, market="spot")` — USDT spot

Run in background: `asyncio.create_task(collect(connectors, sink, max_reconnects=-1))`.
`collect()` supervises via `TaskGroup`, isolates per-connector errors, reconnects with
internal exponential backoff (`min(30, 2**attempt)s + jitter`), and `sink.close()`s on cancel.

### 4.2 QueueSink (the bridge — Entropy defines it; Crypcodile has none)
```python
class QueueSink(Sink):
    def __init__(self, maxsize: int = 200_000) -> None:
        self.q: asyncio.Queue[Record] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0
    async def put(self, record: Record) -> None:
        try: self.q.put_nowait(record)
        except asyncio.QueueFull:                # kHz feed must never block
            try:
                self.q.get_nowait(); self.dropped += 1; self.q.put_nowait(record)  # drop OLDEST
            except (asyncio.QueueEmpty, asyncio.QueueFull): self.dropped += 1
    async def flush(self) -> None: ...
    # close() inherited (await flush -> no-op). MUST stay non-destructive: the TUI owns the queue.
```
Dropping the oldest tick is correct for a *scanner* (last price wins). `dropped` is surfaced
in the status bar.

### 4.3 Universe discovery & symbol keys
- `await connector.list_instruments()` hits public REST (`/products`, `/exchangeInfo`); call
  **once at startup**, populate the registry, cache, pass the same registry into connectors.
- Selection: Coinbase `quote=="USD" & kind==SPOT`; Binance `quote=="USDT" & kind==SPOT`,
  excluding leveraged tokens (`*UP/DOWN/BULL/BEARUSDT`).
- **Liquidity:** neither REST returns 24h volume. v1 uses a **curated majors whitelist**
  (config: `~40–60` liquid pairs) intersected with the discovered set; fallback alphabetical.
- **Symbol-key gotcha (verified):** live Binance records carry `exchange="binance-spot"` and
  `symbol="binance-spot:BTCUSDT"` (venue prefix), which differs from the registry canonical
  `binance:BTCUSDT`. Coinbase is consistent (`coinbase:BTC-USD`). **All TUI/engine state is
  keyed on the live record's `.symbol` string**, never the registry canonical.

### 4.4 Timestamps & record fields
- All ns ints. `local_ts` is always present (`CLOCK_REALTIME`); `exchange_ts` may be `None`
  (Binance `bookTicker`, Coinbase snapshot). **Always order/chart by `local_ts`.**
- `Trade`: `exchange, symbol, symbol_raw, exchange_ts|None, local_ts, id, price, amount, side(Side), liquidation`.
  `amount` is base size; `side` is taker side. The engine reads `symbol, price, amount, side.value, local_ts`.
- Scanner channel set: **`["trade"]`** (last price + highs/lows). Add `"book_ticker"` only if
  the UI shows live spread (≈2× message volume) — out of scope v1.

### 4.5 Chart warmup
- Binance klines via `make_live_backfill().backfill_klines(venue="binance-spot", symbol, interval, start_ns, end_ns)`
  → `OHLCV` (one REST call, ≤1000 bars).
- **Coinbase has no kline/historical-trade backfill** → seed Coinbase charts from the matching
  Binance USDT pair (e.g. `coinbase:BTC-USD` chart warmed from `BTCUSDT`). Slight venue/price
  difference is acceptable for a warmup tail; live ticks then take over.

---

## 5. Equities Simulator

Reuses the **real Crypcodile types** (`Trade`, `Side`) so equity and crypto ticks are
type-identical; only `exchange="sim-equity"` distinguishes them.

- **Universe** (`feeds/equities/universe.py`): `INDICES=(SPY,QQQ,IWM)` + ~180 real tickers
  grouped by sector (megacap, semis, software, finance, health, industrial, energy, consumer,
  volatile). Real symbols make leaderboards look legitimate.
- **Path engine** (`sim.py`, pure/sync): per-symbol **mean-reverting GBM** with per-symbol
  vol/drift/anchor; deterministic given an injected `random.Random(seed)` and `clock_ns`.
  Two event tiers drive lively breadth: (a) idiosyncratic single-name **spikes** (decaying
  overlay), (b) **clustered sector breakouts** (push ~60% of a sector the same direction) so
  new-highs/new-lows move in correlated bursts resembling real regimes.
- **Async feed** (`feed.py`): `EquitySimFeed(sink, seed, ticks_per_sec=4000, clock_ns=now_ns,
  market_hours_gate=None, batch_dt=0.01)` — stamps ns once per batch, builds the same `Trade`,
  `await sink.put(tr)`. Sampling `rng.choice(UNIVERSE)` per tick keeps cost == ticks/sec
  regardless of universe size.
- **Market hours:** default **always-on demo** (`gate=None`); optional `nyse_gate` (09:30–16:00 ET).
- **Determinism:** single shared seeded RNG + stable `UNIVERSE` tuple iteration order. Same
  seed → identical path. (Per-symbol RNG is a documented future option.)

---

## 6. Detection Engine (the core)

Pure, synchronous, no I/O, no logging, no timers. Event-time (`ts_ns` from the record), with a
**per-symbol non-decreasing clamp** (`last_ts = max(last_ts, ts_ns)`) so replays are deterministic.
First observation of a symbol **initializes** its extremes and emits **no** events (baseline) —
this prevents first-tick "new high AND new low" storms.

### 6.1 Rolling extremes — monotonic deque, O(1) amortized (simulation-verified)
```python
WINDOWS_NS = {"30s":30_000_000_000, "1m":60_000_000_000, "5m":300_000_000_000, "20m":1_200_000_000_000}
```
`MonotonicExtreme` keeps a `deque[(ts,price)]` (descending for max / ascending for min):
- `evict(now)`: popleft while `dq[0].ts < now - span`.
- `step(ts, price) -> bool`: `evict`; read `prior = peek()` **before** push; `is_new = prior is None or (price > prior)` (max) — **STRICT `>`** (equalling an extreme is *not* new; avoids flat-tape spam); then back-pop dominated tail and append.
- `SessionExtreme`: 3 floats (`hi, lo, first_price`), no deque; `%Chg = (price-first_price)/first_price`.

`RollingTape` per symbol = 4 windows × 2 sides (`MonotonicExtreme`) + `SessionExtreme` +
`MomentumHorizon` + `last_price`. Deque size is bounded by monotone runs in-window (tens of
entries even at kHz), not tick count.

### 6.2 Momentum — spike / snap-drop / up / down (verified anchor logic)
`MomentumHorizon` keeps a `(ts,price)` deque, retaining one anchor older than `now - span`;
`pct = (price - ref)/ref * 100`. Defaults (all in `EngineConfig`):
```
momentum_horizon_s=5.0  spike_pct=0.40  snapdrop_pct=0.40  upmove_pct=0.15  downmove_pct=0.15
momentum_cooldown_ns=1e9   # per (symbol,kind) throttle
```
Classification (spike/snap-drop take precedence over up/down; cooldown-throttled):
`pct≥spike→SPIKE | pct≥upmove→UPMOVE | pct≤-snapdrop→SNAP_DROP | pct≤-downmove→DOWNMOVE`.
Skip momentum until at least one anchor older than the cutoff exists (sparse-symbol guard).

### 6.3 Breadth & rate
- **Sell%/Buy%**: **amount-weighted (notional)** buy/sell volume over a 30s sliding window →
  `sell_pct = sell_vol/(buy_vol+sell_vol)*100` (matches the original's `S 72% / B 28%` reading).
- **RateMeter** (1s integer buckets, verified): `raw_hz` = ticks in last 1s; `prev30s_rate`
  = events/sec over 30s.
- **accel** flag: compare current event rate to the previous snapshot's; `accelerating` if
  `> prev*(1+0.10)`, `decelerating` if `< prev*(1-0.10)`, else `steady`.
- **Per-window NH/NL counts**: sets of symbols currently at a new high/low per window (decay on
  contrary extreme / timeout) → the dual Lows/Highs gauges.

### 6.4 Leaderboards & snapshot (the 10fps boundary)
The hot path mutates only **O(1) per-symbol counters** (cumulative NH/NL counts since session
start, `last_price`, `session_pct`, `last_momentum_pct`). **No sorting on the hot path.**
`snapshot()` (called ~10×/s by the UI) builds ranked tables with `heapq.nlargest(K, …)`
(`K=20`) → `O(N log K)`, trivial for N≤1000. Returns an **immutable `msgspec.Struct`**:
```python
class LeaderRow:    symbol; count; price; pct_chg
class BreadthSnapshot: sell_pct; buy_pct; raw_hz; prev30s_rate; accel; nh_counts; nl_counts
class EngineSnapshot:  ts_ns; breadth; top_movers; new_highs; new_lows
```

### 6.5 Public API
```python
class Engine:
    def __init__(self, config: EngineConfig | None = None) -> None: ...
    def on_trade(self, symbol, price, amount, side, ts_ns) -> list[Event]: ...   # PURE, sync
    def snapshot(self) -> EngineSnapshot: ...                                    # immutable view
    def reset_session(self, ts_ns: int | None = None) -> None: ...
```
`on_trade` returns `[]` without allocating when no event fires (the common case).

---

## 7. Strategy Console (algo demo)

Pure, deterministic, no real orders. One `Strategy` instance per traded symbol (SPY from the
sim, BTC-USD from real crypto).

- **Default mode: EMA crossover** (`fast=9, slow=21`); `signal=sign(fast_ema - slow_ema)`,
  act only when warm; transition on a *sign change* vs the prior tick (no same-bar re-entry);
  a flip emits `[CLOSE_*, OPEN_*]` in order. Alternate `mode="breakout"` reuses the scanner's
  rolling high/low (breakout of prior-window extreme) — demo synergy with §6.
- **Warmup:** 24 bars (matches "Yahoo warmup: 24 bars"); crypto via Binance klines adapter,
  SPY via synthesized bars from the sim. Emits `INFO`: `"<sym> warmup: 24 bars, EMA ready"`.
- **PnL:** `running_pnl` = raw gross mark `(px-entry)*size` (LONG) / `(entry-px)*size` (SHORT),
  emitted on OPEN + available each tick via `running_pnl(last_px)`; `trade_pnl` = gross −
  entry fee − exit fee, emitted on CLOSE. `fee_bps` default 0 for SPY (clean GIF numbers),
  configurable (~1bp) for BTC. Round only at render.
- **Verified against GIF:** SHORT 748.300→748.435 ⇒ `trade_pnl=-0.135`; LONG 749.886→750.025
  ⇒ `trade_pnl=+0.139`. These are golden tests.
- **Log format** (`render_event` → `(text, color)`): `OPEN LONG @ {px:.3f} running_pnl={..:.3f}`
  (green), `OPEN SHORT …` (red), `CLOSE LONG/SHORT @ {px:.3f} trade_pnl={..:.3f}` (yellow),
  `INFO` white. Reconnect/disconnect noise lines are owned by the **transport/console layer**
  (pushed as INFO lines), keeping the engine pure.
- **API:** `warmup(bars) -> list[StrategyEvent]`; `on_price(symbol, price, ts_ns) -> list[StrategyEvent]`
  (mismatched symbol → `[]`); `position`, `is_warm`, `running_pnl(last_px)`.

---

## 8. UI Layer (Textual — verified API)

**Verdict (validated against current Textual + textual-plotext): the design is achievable.**

### 8.1 Layout (`compose()` + TCSS)
Top-level `Vertical`: `HeaderBar` (h=3) → `Horizontal #body` (1fr) → `StatusBar` (h=1).
`#body` = three columns: `AlgoConsole` (RichLog, w=32) | `#center` (1fr) | `#charts` (w=48).
`#center` (Vertical): `TickerStrip` → `HighLowGauges` → `EventHistogram` → `Horizontal`
(`NewLowsBoard` | `SessionHighsBoard`). **Raw/dense look entirely in TCSS**: `background:black;
border:none; padding:0` everywhere, fixed small heights, hex green/red/yellow via a registered
`Theme`. No grid spanning needed; nested `Horizontal`/`Vertical` suffices.

### 8.2 Live update mechanism
- `@work(thread=True, exclusive=True, group="drain")` worker: blocking `queue.get()` at kHz →
  `engine.on_trade(...)` + `strategy.on_price(...)`; writes algo lines via
  `self.call_from_thread(rich_log.write, line)` (**only** safe cross-thread path).
- `set_interval(1/10, sample_snapshot)` on the event loop: `snap = engine.snapshot()` → assign
  to widget **reactives** (`watch_*` repaints). Boards may refresh at 4–5 fps if needed.
- The drain worker only touches engine/queue + `call_from_thread`; all widget reads/writes for
  the repaint happen in the event-loop sampler (no `call_from_thread` there).

### 8.3 Widgets
- **AlgoConsole**: `RichLog(markup=True, auto_scroll=True, max_lines=2000)` (newest at bottom).
- **Boards**: `DataTable` with per-cell `rich.text.Text` color, columns `Symbol/Count/Price/%Chg`,
  top-N ≤ 25 rows, right-justified fixed-width numerics. Prefer in-place `update_cell_at` + `sort()`
  over `clear()`+repopulate if profiling shows table cost; small N → `clear()`+`add_row` is fine.
- **Charts**: **`textual-plotext` `PlotextPlot`** subclasses (recommended, lowest risk) —
  `PriceChart` overrides `replot()` → `self.plt.candlestick(dates, {Open,Close,High,Low})`;
  `VolumeChart` → `self.plt.bar(...)`. A hand-drawn `render_line` candlestick (one column per
  candle: `█` body in the open–close band, `│` wick in the low–high band, color by up/down;
  half-blocks for 2× vertical resolution) is the **documented fallback** if plotext chrome
  clashes with the raw look.
- **Gauges** (`GaugeBar`, render_line): proportional fills via 1/8-width block chars
  (`▏▎▍▌▋▊▉█`); high/low gauges + the Sell%/Buy% split bar (two-color around center).
- **HeaderBar / TickerStrip / EventHistogram / StatusBar**: custom `Static`/`render_line`
  widgets fed by reactives.

### 8.4 Keybindings & modals
`BINDINGS = [("s","settings","Settings"), ("question_mark","help","Help"), ("h","help","Help"),
("e","errors","Errors"), ("q","quit","Quit")]` (bind both `?` and `h` since shifted-punctuation
delivery is terminal-dependent). `action_*` push `ModalScreen` subclasses
(`HelpScreen/SettingsScreen/ErrorScreen`); data keeps flowing underneath while open.

### 8.5 Theme
One `Theme(name="entropy", background="#000000", foreground="#c8c8c8", success=green,
error=red, warning=yellow, dark=True)`, `register_theme` + `self.theme="entropy"` in `on_mount`.

---

## 9. Performance & Concurrency

- **kHz → 10fps decoupling** keeps CPython responsive for ~300–500 symbols at thousands of
  ticks/sec on one core.
- **Hot-path discipline:** `__slots__`/`slots=True` everywhere; `deque(maxlen=…)` for rolling
  state; integer ns timestamps; no f-strings/format in `on_trade`; `on_trade` returns `[]`
  without alloc when idle; structs built only for emitted events + the snapshot.
- **Backpressure = drain-ALL-per-frame** with a per-frame safety cap (e.g. 250k) and monotonic
  `loop.time()` pacing that skips ahead instead of spiraling when behind.
- **GC:** `gc.freeze()` after building the universe/engine so long-lived dicts are excluded from
  every collection; tune thresholds if pauses appear.
- **Leaderboards:** never sort the full book; `heapq.nlargest/nsmallest(K)` over a single
  precomputed `(sym, gain, vol)` list per frame.
- `QueueSink.dropped` surfaced in the status bar so overload is visible, not silent.

---

## 10. Configuration & Resolved Defaults

All thresholds live in config (`EngineConfig`, `StrategyConfig`, `AppConfig`). Decisions taken
to remove ambiguity (overridable):

| Decision | Default |
|---|---|
| Crypto universe | curated majors whitelist (~40–60 pairs), Coinbase USD + Binance USDT spot |
| Cross-venue unify | No — distinct rows keyed by live `.symbol` |
| Equity universe | INDICES + ~180 real tickers, sector-grouped |
| Equity ticks/sec | 4000 aggregate, always-on demo (no market-hours gate) |
| New-extreme test | STRICT `>` (equal ≠ new) |
| New-symbol first tick | initialize baseline, emit nothing |
| Windows | 30s / 1m / 5m / 20m / session |
| Momentum | single 5s horizon; spike/snap 0.40%, up/down 0.15%, 1s cooldown |
| Sell%/Buy% | amount-weighted notional over 30s |
| Leaderboard | top-20 (≤25 rows), Count = cumulative NH/NL since session start |
| Charts | textual-plotext candlestick (render_line fallback documented) |
| Strategy | EMA(9,21) crossover, 24-bar warmup, per-tick flips, fee_bps 0 (SPY) / ~1 (BTC) |
| Persistence | none (ephemeral live-only) |
| Help key | both `?` and `h` |

---

## 11. Testing Strategy (TDD)

Pure modules are built test-first. Highest correctness risk first:
`MonotonicExtreme`/`SessionExtreme` → `RateMeter` → `MomentumHorizon` → `Engine` wiring →
`snapshot`/leaderboards → `Strategy`. The simulation-verified deque/horizon traces become
fixtures.

**Engine fixtures** (input sequence → expected events): rolling-max new-high boundary
(`span=100ns` trace), equal-price → no event (strict), eviction at window edge, session
high/low, momentum `+7.463%` over 5s anchor, RateMeter `3.0/s` steady + burst, out-of-order /
decreasing ts clamp, first-tick baseline (no event).

**Strategy fixtures** (from validated matrix): golden long-then-flip with exact ts/price/pnl;
fee math (`-2.200` at 10bps); SHORT sign (`748.3→748.435 = -0.135`, `749.886→750.025 = +0.139`);
breakout flip; long-only guard; symbol mismatch → `[]`; determinism (two runs identical);
`running_pnl` mark.

**Sim:** determinism (same seed → identical path), event-injection liveliness, type identity
with Crypcodile `Trade`.

**Perf microbench:** assert single-thread engine throughput (≥ a few×10⁵ ticks/s).

**UI:** Textual test harness smoke (compose, snapshot assignment, no crash); manual visual check.

**Live smoke:** a no-TUI script draining `start_feed()` printing real Coinbase/Binance trades.

---

## 12. Build Phases (detailed in the implementation plan)

1. **Project setup** — `pyproject.toml` (deps: `textual`, `textual-plotext`, `msgspec`, and
   `crypcodile` as editable path dep), `uv` env, package skeleton, ruff+mypy config, first commit.
2. **Feed bus** — `QueueSink`; equity simulator (sim + feed + universe); a no-TUI smoke that
   prints sim ticks; then crypto wiring (`discover_universe`, `build_live`, `start_feed`) with a
   real Coinbase/Binance smoke.
3. **Engine (TDD)** — windows, rate, momentum, breadth, leaderboard, `Engine`, `snapshot`.
4. **Strategy (TDD)** — ema, engine, format, warmup adapters.
5. **UI panels** — app skeleton, theme/TCSS, header, ticker strip, gauges, histogram, boards,
   status bar; wire the 10fps sampler + drain worker.
6. **Charts** — textual-plotext price + volume; warmup seeding.
7. **Algo console** — RichLog wiring + transport noise lines.
8. **Polish** — keybindings, modals (help/settings/errors), `dropped` gauge, perf tuning
   (`gc.freeze`, drain cap), responsive minimum-size handling.

---

## 13. Dependencies & Setup
- Python 3.12 (pinned). `uv` for env/locking (Crypcodile-consistent).
- Runtime: `textual`, `textual-plotext`, `msgspec`, `crypcodile` (editable path dep
  `/Users/nazmi/Crypcodile`; brings `aiohttp`, `websockets`, `polars`, `duckdb`).
- Dev: `pytest`, `pytest-asyncio`, `pytest-timeout`, `ruff`, `mypy`.

---

## 14. Key Risks & Mitigations (from design hardening)
| Risk | Mitigation |
|---|---|
| Forgetting `connector.transport` (latent in Crypcodile examples) | single `build_live()` helper sets it; assert non-None |
| Binance live symbol prefix `binance-spot:` ≠ registry canonical | key all state on live `.symbol`; align warmup `venue="binance-spot"` |
| `exchange_ts` None | always use `local_ts` |
| kHz feed stalling socket on slow UI | bounded `QueueSink` + `put_nowait` drop-oldest |
| Coinbase no kline warmup | seed from matching Binance USDT pair |
| New-symbol / flat-tape event storms | strict `>`, first-tick baseline (no emit) |
| Out-of-order ts | per-symbol non-decreasing clamp |
| DataTable repaint cost at 10fps | top-N rows, `update_cell_at`+`sort`, or lower board fps |
| Custom candlestick complexity | use textual-plotext; render_line is documented fallback |
| Touching widgets from drain thread | strict split; `call_from_thread` only |
| `?` key delivery | also bind `h` |

---

## 15. Out of Scope (v1) — deferred
Real equities feed; cross-venue symbol unification; persistence/replay lake; order-book
(L2) channels & spread display; multi-horizon momentum; web/desktop clients; user-editable
universe at runtime.
