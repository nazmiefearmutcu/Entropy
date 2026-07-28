# Entropy — native macOS cockpit

A non-terminal, natively-installable macOS GUI for Entropy. It reuses the existing
`entropy` engine + feeds **unchanged** (headless) and presents the live market-scanner
cockpit — breadth, new-high/low boards, a focus-symbol candlestick chart, the depth
ladder, and a watchlist — in a real windowed app. Coexists with the Textual TUI over
the same core; the GUI is a second frontend.

## Architecture

```
Tauri 2 (Rust shell)  ──spawns──►  Python sidecar (FastAPI + uvicorn)
   native window                      reuses entropy engine + sim/live feeds, headless
   reads PORT= from stdout            /ws/live  → SnapshotMessage @10Hz
   injects window.__SIDECAR_PORT__    POST /api/command → parse_command (TUI grammar)
        │
        └──loads──►  React + Vite + Tailwind frontend
                       lightweight-charts (candles), 9 cockpit panes, depth ladder
                       StreamClient (WS + reconnect) → panes render from the snapshot
```

- `sidecar/`  — FastAPI app importing `entropy`; streams `EngineSnapshot`-derived JSON.
- `frontend/` — React cockpit; consumes the WS stream, posts commands.
- `tauri/`    — Rust shell; spawns/supervises the sidecar, opens the native window.

The frontend also runs against a synthetic feed with `?mock=1`, which is how the layout can
be worked on without a backend. `src/mock.ts` mirrors `GET /api/meta` **exactly** — an
invented vocabulary there is worse than no mock at all, since it hides contract drift instead
of catching it.

## Develop

```bash
# 1. sidecar deps
cd native/sidecar && uv sync

# 2. frontend deps
cd native/frontend && npm install

# 3. run: start Vite, then Tauri dev (which auto-spawns the sidecar)
cd native/frontend && npm run dev            # terminal A → http://localhost:5173
cd native/tauri     && cargo tauri dev       # terminal B → opens the app window
```

Tests: `cd native/sidecar && uv run pytest -q` · `cd native/frontend && npx vitest run`.

The frontend also runs standalone in a browser against a running sidecar:
`http://localhost:5173/?port=<sidecar_port>` (the sidecar prints `PORT=<n>` on stdout).

## Build the `.app`

```bash
# 1. freeze the sidecar to a self-contained binary (no system Python needed)
cd native/sidecar && uv run pyinstaller entropy_sidecar.spec --noconfirm

# 2. drop it where Tauri expects the externalBin (target-triple suffix)
cp dist/entropy_sidecar ../tauri/src-tauri/binaries/entropy_sidecar-aarch64-apple-darwin

# 3. build the frontend + bundle the .app
cd native/frontend && npm run build
cd native/tauri    && cargo tauri build      # → src-tauri/target/release/bundle/macos/Entropy.app

# 4. install
cp -R src-tauri/target/release/bundle/macos/Entropy.app /Applications/
```

The packaged app spawns the bundled sidecar binary next to its executable, so it runs
from Finder with no `uv`/Python installed. In dev the shell falls back to `uv run`.

## Scope

Scanner boards, breadth, activity ticker, focus chart, depth ladder, watchlist, the `:`
command bar, a symbol picker, independent cadence controls, a full settings drawer and the
bot console. Still deferred: the walk-forward calibration UI, and editing live-exchange
credentials from the GUI (they stay in the config file on purpose).

## Contract

The sidecar speaks `schema_version: 2`. Snapshots stream over `/ws/live` at ~10 Hz and carry
`settings`, `feeds` and `bot` alongside the market data, so the frontend never has to guess
what the engine is currently configured to do.

REST surface:

| Route | Purpose |
|---|---|
| `GET /api/meta` | every vocabulary the UI offers (timeframes, chart intervals, themes, strategies, risk profiles, vote/normalize/exit modes) plus the settings path |
| `GET`/`PUT /api/settings` | full app+bot config; PUT deep-merges, validates both halves as one unit, and applies nothing if anything is invalid |
| `GET /api/symbols?q=` | universe search, with a `watched` flag per row |
| `GET`/`POST /api/watchlist`, `DELETE /api/watchlist/{symbol}` | the persistent watchlist |
| `POST /api/focus` | move the chart |
| `POST /api/bot/{start,stop,pause,resume,halt}` | bot lifecycle |
| `POST /api/command` | the `:` grammar |

Two rules the sidecar holds to, because breaking either is what made the earlier version feel
hollow: **a mutating endpoint never reports success for something it did not do** (an
unsupported verb returns `ok=false`, it does not "acknowledge"), and **settings that fail
validation change nothing** rather than applying the half that parsed.

### Three cadences, deliberately separate

`app.timeframe` is the scanner's rolling-window cadence. `app.chart_interval` is the candle
width (`""` follows the timeframe). `bot.timeframe` / `bot.bar_s` is the bot's own clock.
These were previously one knob or hardcoded; the UI names all three side by side so the
distinction is visible rather than folklore.
