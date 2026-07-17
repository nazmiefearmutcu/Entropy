# Entropy

**A real-time terminal market scanner, algo console, and trading bot — in your terminal.**

Entropy is a [Textual](https://textual.textualize.io/) TUI that streams live crypto and real US
equities (with a deterministic simulator fallback), aggregates them into candlestick charts, and
runs a breadth/"entropy" engine that surfaces new highs & lows, spikes, and snap-drops across
multiple rolling windows — all on a selectable **15-minute-centric timeframe**.

![Entropy running — live candlestick charts, breadth gauges, and the new-highs/lows scanner on the 15m timeframe](docs/assets/entropy.png)

---

## Highlights

- **15-minute operating timeframe, fully selectable.** Candles, scanner windows (`15m / 1h / 4h /
  session`), momentum horizon, breadth, and warmup all derive from one timeframe abstraction.
  Switch between `1m / 5m / 15m / 1h / 4h` live from the Settings screen — the engine and charts
  reconfigure on the fly.
- **Live crypto + live US equities.** Real crypto ticks (Coinbase / Binance via
  [crypcodile](https://github.com/nazmiefearmutcu/Crypcodile)) alongside real US-equity data via
  [stockodile](https://github.com/nazmiefearmutcu/stockodile) — keyless by default, upgraded by
  optional API keys — with a deterministic equities simulator as fallback, all unified through one
  engine. `--equity-source sim|live|auto` picks the mode (`auto` goes live while NYSE is open); an
  **NYSE OPEN/CLOSED** chip in the header shows the market state.
- **Watchlist & symbol search.** `/` opens a ranked symbol search over ~500 US tickers (SEC EDGAR
  snapshot, refreshable via a 24h cache) plus the crypto majors; `w` toggles the focused symbol on
  a watchlist persisted to `~/.entropy/watchlist.json`. The watchlist board shows last / Δ% / a
  sparkline per symbol, and selecting any board row focuses it.
- **Focus-symbol charting & quote panel.** Chart #1 follows whatever symbol you focus (search,
  boards, or the command bar), with EMA9/21 overlays, up/down-colored volume, timeframe-aware
  axes, and throttled redraws. A quote panel shows last / Δ% / session hi-lo, plus fundamentals
  (P/E, market cap, 52-week hi/lo) for live equities.
- **Command bar.** `:` opens a mini command line — `chart SYM · watch/unwatch SYM ·
  tf 1m|5m|15m|1h|4h · theme NAME · source sim|live|auto · help`.
- **Breadth / entropy engine.** Per-window new-high / new-low detection (O(1) monotonic windows),
  session extremes, spikes, snap-drops, buy/sell breadth, and an activity ticker.
- **Candlestick & line charts** with a toggleable volume pane, for both the crypto and equity legs.
- **A clean, sectioned Settings screen** — appearance, timeframe, data feeds, and scanner/engine
  thresholds, with live hot-apply (including feed toggles and the sim tick rate) and input
  validation. 7 built-in themes.
- **An automated trading bot** (`entropy bot`) — paper core with risk profiles, a live-execution
  scaffold, and a TUI dashboard — running on its own sub-second momentum cadence. Ships a
  multi-indicator **consensus** strategy (default, alongside `ema_cross`): a weighted EMA + MACD +
  RSI + Bollinger vote with a volatility/trend regime filter and exit hysteresis. This targets
  better *signal quality* on the synthetic/live streams it runs on — it is not a claim of
  live-market edge.
- **Strategy calibration & benchmarks** — grid-search back/forward accuracy tests, **walk-forward
  K-fold calibration** (`entropy calibrate --walk-forward N`) with per-fold out-of-sample metrics,
  the worst fold and parameter stability surfaced (no cherry-picking), and throughput/latency
  benchmarks from the CLI. All calibration output carries a disclaimer: metrics come from a seeded
  simulator and imply no live edge.
- **Hardened plumbing.** NaN-tick guards and non-decreasing clocks in the engine, an append-only
  trade CSV, and settings that hot-apply without a restart.

## Quick start

Entropy uses [uv](https://docs.astral.sh/uv/). From a clone:

```bash
uv sync            # resolve deps (pulls the crypcodile + stockodile feed packages from GitHub)
uv run entropy ui  # launch the scanner dashboard
```

Requires Python ≥ 3.12. The [crypcodile](https://github.com/nazmiefearmutcu/Crypcodile) (crypto)
and [stockodile](https://github.com/nazmiefearmutcu/stockodile) (equities) feed packages are
resolved automatically from GitHub via `[tool.uv.sources]`.

## Usage

```bash
uv run entropy ui                        # main TUI scanner dashboard (default)
uv run entropy ui --equity-source auto   # live equities while NYSE is open, sim otherwise
uv run entropy bot                       # automated trade bot CLI / TUI
uv run entropy calibrate                 # calibrate strategies + back/forward accuracy tests
uv run entropy calibrate --walk-forward 4  # walk-forward K-fold OOS calibration
uv run entropy benchmark                 # system throughput & latency benchmarks
```

Inside the dashboard:

| Key       | Action                                            |
|-----------|---------------------------------------------------|
| `/`       | Symbol search (US tickers + crypto majors)        |
| `w`       | Toggle the focused symbol on the watchlist        |
| `:`       | Command bar (`chart` / `watch` / `tf` / `theme` / `source` / `help`) |
| `s`       | Settings                                          |
| `?` / `h` | Help                                              |
| `e`       | Errors console                                    |
| `q`       | Quit                                              |

## Equity data sources

Equities run in one of three modes — `sim`, `live`, or `auto` (live while the US market is open
per the NYSE calendar, sim otherwise) — set via `--equity-source` or the Settings screen. In live
mode, stockodile picks one provider from the environment:

| Provider           | Keys                                   | Data                                   | Symbols |
|--------------------|----------------------------------------|----------------------------------------|---------|
| **Google Finance** | none (default)                         | last-price quotes polled every ~10s    | no cap  |
| **Alpaca**         | `ALPACA_API_KEY` + `ALPACA_API_SECRET` | real IEX trades over websocket         | 30      |
| **Finnhub**        | `FINNHUB_API_KEY`                      | real trades over websocket             | 50      |

The keyless Google Finance default is an **approximation**: scraped last prices are re-emitted as
synthetic trades (tick-rule sides), not exchange prints — fine for scanning, not for microstructure.
Set the Alpaca or Finnhub keys for real trade feeds. Charts warm up from real 15-minute Yahoo bars,
and the crypto leg (crypcodile) is unchanged alongside.

## Timeframes

The whole terminal is parameterized by a single timeframe registry. The default is **15m**; each
timeframe defines its bar interval, three rolling scanner windows, and the momentum/breadth cadence:

| Timeframe | Bar    | Scanner windows   |
|-----------|--------|-------------------|
| 1m        | 1 min  | 1m / 5m / 15m     |
| 5m        | 5 min  | 5m / 15m / 1h     |
| **15m**   | 15 min | **15m / 1h / 4h** |
| 1h        | 1 hr   | 1h / 4h / 1d      |
| 4h        | 4 hr   | 4h / 12h / 1d     |

(Plus the cumulative **session** high/low, always tracked.)

## Architecture

```
src/entropy/
  engine/    breadth/entropy engine, rolling windows, candle aggregation, timeframe registry
  feeds/     live crypto (crypcodile) + equities (stockodile live / sim / auto), kline warmup
  data/      symbol universe (SEC EDGAR + crypto majors) and the persistent watchlist
  strategy/  EMA / breakout signal engine used by the live TUI
  ui/        Textual app + widgets (charts, quote panel, search, command bar, watchlist,
             gauges, ticker, boards, settings modals), themes
  bot/       standalone trading bot — strategies (consensus, ema_cross, …), risk profiles,
             portfolio, calibration, runner, dashboard
  config.py  engine config (+ per-timeframe derivation)
  app.py     AppConfig
```

The main app runs on the selected timeframe (via `EngineConfig.from_timeframe(...)`), while the bot
keeps its own legacy sub-minute cadence — the two coexist through the same engine without
interfering.

## Development

```bash
uv run pytest             # full test suite
uv run ruff check src tests
uv run mypy src
```

## License

Apache-2.0.
