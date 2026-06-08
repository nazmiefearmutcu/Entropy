Entropy is a real-time terminal (TUI) market scanner and algo console that streams live crypto via the Crypcodile engine, simulates US equities on the same record path, detects multi-window new highs/lows and momentum, ranks movers, runs a demo EMA scalping strategy, and renders live candlestick charts — all in a single asyncio process driven by Textual.

---

## Screenshot

```
╔ Entropy ════════════════════════════════════════════════════════════════════╗
║ Algo Console         │  SPY MSFT NVDA AAPL TSLA BTCUSDT ETHUSDT …          ║
║ BUY  SPY  @483.21    │  Buy%  ██████░░░░  64%   Sell% ██░░░░░░  36%        ║
║ SELL SPY  @483.09    │  Rate  ████████░░  4.1k  Accel ↑                    ║
║ BUY  SPY  @483.35    ├─────────────────────────────────────────────────────╢
║ …                    │  New Highs            │  Session Highs               ║
║                      │  NVDA   +0.81% (12)   │  MSTR  +2.14%               ║
║                      │  MSFT   +0.54% ( 8)   │  COIN  +1.87%               ║
║                      │  …                    │  …                           ║
║                      ├───────────────────────╢                              ║
║                      │         BTCUSDT  1-s candle chart                    ║
║                      │  67 420 ┤ ╷ ╷  ╷╷╷╷                                 ║
║                      │  67 300 ┤╶╴╶╴╶╴╶╴╶╴  (volume below)                 ║
╚══════════════════════╧═════════════════════════════════════════════════════╝
```

The console (left) logs EMA-strategy signals in real time. The center panel shows a live ticker strip, buy/sell breadth gauges, a momentum histogram, and ranked leaderboards. The right panel renders a 1-second candlestick chart with a volume bar chart below it.

---

## Requirements

| Dependency | Version |
|---|---|
| Python | 3.12 |
| [Crypcodile](https://github.com/nazmiefearmutcu/Crypcodile) | editable local path |
| uv | any recent |

Crypcodile must be checked out locally. The default path in `pyproject.toml` is
`/Users/nazmi/Crypcodile`; adjust `[tool.uv.sources]` if yours differs.

---

## Install

```sh
# 1. Clone and enter
git clone https://github.com/<you>/Entropy && cd Entropy

# 2. Make sure Crypcodile is available at the path in pyproject.toml,
#    or edit [tool.uv.sources] to match your local checkout.

# 3. Create the virtualenv and sync all dependencies (including Crypcodile editable)
uv sync

# 4. Activate (optional — `uv run` works without this)
source .venv/bin/activate
```

---

## Run

```sh
# With activated venv
python -m entropy

# Or via uv (no activation needed)
uv run python -m entropy
```

The app starts immediately: the equity simulator fires at ~4 000 ticks/s and the
crypto feed connects to Binance WebSocket. Both push `Trade` records into the same
engine, so the leaderboards and gauges reflect a blended universe from the first
second.

Press **q** to quit cleanly (graceful shutdown of all feed tasks).

---

## Data Sources

### Real crypto — Binance WebSocket (live)

Crypcodile's `collect()` subscribes to the `trade` channel for 14 liquid spot pairs:

```
BTCUSDT  ETHUSDT  SOLUSDT  XRPUSDT  DOGEUSDT  ADAUSDT  AVAXUSDT
LINKUSDT LTCUSDT  BCHUSDT  DOTUSDT  UNIUSDT   AAVEUSDT XLMUSDT
```

On startup the engine also fetches the last 24 one-minute Binance klines for
`BTCUSDT` (configurable via `crypto_strategy_symbol`) to warm up the EMA strategy
before the first live tick arrives.

No API key is needed. Binance public WebSocket endpoints are unauthenticated.

### Simulated US equities

A deterministic GBM simulator (seeded, mean-reverting) generates ~4 000 ticks/s
across 3 indices + ~120 stocks drawn from nine sectors:

| Sector | Tickers (sample) |
|---|---|
| Index | SPY, QQQ, IWM |
| Megacap | AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA |
| Semis | AMD, INTC, MU, QCOM, ASML, AMAT |
| Software | CRM, SNOW, PLTR, CRWD, NET, DDOG |
| Finance | JPM, GS, V, MA, BAC |
| Health | UNH, MRK, ABBV, TMO, AMGN |
| Industrial | GE, CAT, DE, BA, HON |
| Energy | XOM, CVX, COP, SLB |
| Volatile | GME, MSTR, RIVN, MARA, COIN |

The simulator produces the same tick sequence for any given seed, making
benchmark runs reproducible.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `s` | Open Settings modal (shows live `AppConfig`) |
| `h` / `?` | Open Help modal |
| `e` | Open Errors modal (last engine/feed error) |
| `q` | Quit |

---

## Configuration

All knobs live in two `msgspec.Struct` classes in `src/entropy/app.py` and
`src/entropy/config.py`.

### `AppConfig` (top-level)

| Field | Default | Description |
|---|---|---|
| `seed` | `42` | RNG seed for the equity simulator |
| `equity_tps` | `4000` | Simulated equity ticks per second |
| `enable_crypto` | `True` | Connect to Binance WebSocket |
| `enable_equities` | `True` | Run the equity simulator |
| `strategy_symbol` | `"SPY"` | Equity symbol tracked by the EMA strategy |
| `crypto_strategy_symbol` | `"binance-spot:BTCUSDT"` | Crypto symbol tracked by the EMA strategy |
| `engine` | `EngineConfig()` | Detection engine parameters (see below) |

### `EngineConfig` (detection engine)

| Field | Default | Description |
|---|---|---|
| `windows_ns` | `{30s, 1m, 5m, 20m}` | Rolling windows for new-high/low detection (integer ns) |
| `momentum_horizon_s` | `5.0` | Look-back window for momentum % calculation |
| `spike_pct` | `0.40` | Price-spike threshold (fraction of price) |
| `snapdrop_pct` | `0.40` | Snap-drop threshold (fraction of price) |
| `upmove_pct` | `0.15` | Up-move threshold |
| `downmove_pct` | `0.15` | Down-move threshold |
| `momentum_cooldown_ns` | `1_000_000_000` | Minimum gap between momentum events per symbol (ns) |
| `new_extreme_strict` | `True` | Strict (`>`) vs non-strict (`>=`) new-high/low comparison |
| `breadth_window_s` | `30` | Rolling window for buy/sell breadth calculation (seconds) |
| `leaderboard_k` | `20` | Max rows in each leaderboard |
| `accel_eps` | `0.10` | Acceleration threshold for breadth rate change label |

To override defaults, construct `AppConfig` explicitly before passing to `EntropyApp`:

```python
from entropy.app import AppConfig
from entropy.config import EngineConfig
from entropy.ui.app import EntropyApp

cfg = AppConfig(
    seed=99,
    equity_tps=2000,
    enable_crypto=False,
    engine=EngineConfig(leaderboard_k=10, momentum_horizon_s=10.0),
)
EntropyApp(cfg).run()
```

---

## Architecture

```
Binance WS ──► CrypcodileFeed ─┐
                                ├─► QueueSink (asyncio.Queue, bounded)
EquitySimFeed ─────────────────┘         │
                                          │  @work(thread=True) drain worker
                                          ▼
                               Engine.on_trade()          Strategy.on_trade()
                               (pure, sync, no I/O)       (EMA scalper, sync)
                                          │                       │
                               Engine.snapshot()           StrategyEvent list
                                          │                       │
                               set_interval(1/10) ◄──────────────┘
                                          │
                               Textual widgets repaint @ ~10 fps
```

- **Single process, single asyncio loop.** No extra threads except the bounded queue drain worker.
- **Engine is pure:** takes `(symbol, price, amount, side, ts_ns)` primitives, returns
  frozen `Event` structs. No I/O, no clock reads, no logging.
- **Snapshot immutability:** `Engine.snapshot()` returns a frozen `EngineSnapshot`
  (`msgspec.Struct, frozen=True`), safe to pass across thread boundaries.
- **Backpressure:** the `QueueSink` is bounded (`maxsize=50_000`); feeds block if the
  engine falls behind.

---

## Development

```sh
# Run tests
pytest -q

# Lint (auto-fix)
ruff check --fix src/ tests/

# Type-check
mypy src/entropy/

# Run a single test file
pytest tests/test_engine.py -v
```

The test suite covers the detection engine, rolling windows, breadth tracker,
leaderboard ranking, strategy EMA logic, equity simulator, and Textual widgets
(via `App.run_test` pilot). All tests are hermetic and run offline (no network).

---

## License

Apache-2.0
