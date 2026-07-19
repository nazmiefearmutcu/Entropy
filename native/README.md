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

MVP: scanner boards + breadth + activity ticker + focus chart + depth ladder + watchlist
+ `:` command bar. Deferred: bot/algo console, walk-forward calibration UI, full settings.
