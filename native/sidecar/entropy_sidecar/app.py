from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from typing import Any

import msgspec
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from entropy import settings as settings_store
from entropy.app import AppConfig
from entropy.bot.config import STRATEGY_NAMES, BotConfig
from entropy.bot.config import validate as validate_bot
from entropy.bot.config import warnings as warnings_bot
from entropy.bot.risk.profiles import PRESETS
from entropy.bot.strategies.consensus import EXIT_MODES, NORMALIZE_MODES, VOTE_MODES
from entropy.engine.timeframe import CHART_INTERVALS, TIMEFRAMES
from entropy_sidecar.contract import (
    EQUITY_SOURCES,
    THEMES,
    CommandRequest,
    CommandResult,
    SettingsPatch,
    SymbolRequest,
)
from entropy_sidecar.stream import SnapshotSource, validate_app

# The sidecar binds 127.0.0.1 only and is consumed solely by the Tauri shell
# (origin tauri://localhost on macOS) and the Vite dev server. Restrict CORS to
# those origins rather than a wildcard (avoids the permissive-CORS smell).
_ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://tauri.localhost",   # Tauri v2 + WebView2 serves the app from here
    "https://tauri.localhost",
]

# Sentinel a GET /api/settings client sees instead of live API credentials;
# a PUT carrying it back is dropped before the merge (write-only secrets).
REDACTED_SECRET = "\ufffdredacted"


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge; ``patch`` wins at the leaves.

    Recursive rather than shallow because the configs nest (bot.consensus,
    bot.live, app.engine): a shallow merge would replace a whole sub-object with
    the partial one, and re-decoding that resets every field the caller did not
    mention back to its default.
    """
    out = dict(base)
    for key, value in patch.items():
        current = out.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            out[key] = _deep_merge(current, value)
        else:
            out[key] = value
    return out


def _unknown_keys(base: dict[str, Any], patch: dict[str, Any], prefix: str) -> list[str]:
    """Dotted paths in ``patch`` that the current config has no field for.

    msgspec.convert IGNORES unknown keys, so without this a PUT of a misspelled
    field would return ok=true having changed nothing — the exact lie this
    rewrite exists to remove.
    """
    unknown: list[str] = []
    for key, value in patch.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in base:
            unknown.append(path)
        elif isinstance(value, dict) and isinstance(base[key], dict):
            unknown.extend(_unknown_keys(base[key], value, path))
    return unknown


def _result(ok: bool, message: str, problems: list[str] | None = None) -> dict[str, Any]:
    return {"ok": ok, "message": message, "problems": problems or []}


def _install_auth(app: FastAPI, auth_token: str) -> None:
    """Require the per-process token on every /api route (401 otherwise).

    CORS alone cannot stop cross-site "simple requests" (POSTs without a
    preflight), so any webpage in any browser could previously drive the bot;
    with a token the drive-by request cannot carry the secret. /health stays
    open (no data). The Tauri shell prints and injects the token; it is not
    accepted from the URL on HTTP routes (never logged, never in history)."""
    import hmac

    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _require_token(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-sidecar-token", "")
            if not hmac.compare_digest(supplied, auth_token):
                return JSONResponse(
                    {"ok": False, "message": "unauthorized",
                     "problems": ["missing or invalid sidecar token"]},
                    status_code=401,
                )
        return await call_next(request)


def _from_tuple(res: tuple[bool, str, list[str]]) -> dict[str, Any]:
    ok, message, problems = res
    return _result(ok, message, problems)


def create_app(*, source: SnapshotSource | None = None, tick_hz: float = 10.0,
               auth_token: str | None = None) -> FastAPI:
    src = source or SnapshotSource()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Start the feeds + drain on server startup, stop on shutdown.
        # (TestClient without a `with` block never triggers lifespan, so unit
        # tests that seed the engine directly stay feed-free and deterministic.)
        await src.start_feeds()
        try:
            yield
        finally:
            await src.stop_feeds()

    app = FastAPI(title="entropy-sidecar", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=_ALLOWED_ORIGINS,
        allow_methods=["*"], allow_headers=["*"],
    )
    if auth_token:
        _install_auth(app, auth_token)
    app.state.source = src
    interval = 1.0 / tick_hz

    async def _decode(request: Request, kind: type) -> Any:
        """Decode a msgspec body — FastAPI can't use a msgspec.Struct as a
        Pydantic body model, so parse it directly (keeps the contract
        single-sourced). Returns None when the body is unusable."""
        try:
            return msgspec.json.decode(await request.body(), type=kind)
        except (msgspec.DecodeError, msgspec.ValidationError):
            return None

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, bool]:
        return {"ready": True}

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        # Browser clients always carry an Origin header: it must be the app's
        # own. Non-browser loopback clients (tests, TUI probes) send none —
        # they pass via the per-process token instead.
        origin = ws.headers.get("origin")
        if origin is not None and origin not in _ALLOWED_ORIGINS:
            await ws.close(code=1008)
            return
        if auth_token and not hmac.compare_digest(
            ws.query_params.get("token", ""), auth_token
        ):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while True:
                msg = await app.state.source.build()
                # send TEXT (not bytes): the browser frontend does JSON.parse on a
                # string frame; a binary frame would arrive as a Blob and break it.
                await ws.send_text(msgspec.json.encode(msg).decode())
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            return

    # --- vocabularies ---------------------------------------------------------

    @app.get("/api/meta")
    async def meta() -> dict[str, Any]:
        """Every enumerable choice the settings UI needs, sourced from the code
        that enforces them — so a new timeframe or strategy shows up in the GUI
        without a second list to keep in sync."""
        return {
            "timeframes": list(TIMEFRAMES),
            "chart_intervals": list(CHART_INTERVALS),
            "themes": list(THEMES),
            "strategies": list(STRATEGY_NAMES),
            "risk_profiles": [msgspec.to_builtins(p) for p in PRESETS.values()],
            "vote_modes": list(VOTE_MODES),
            "normalize_modes": list(NORMALIZE_MODES),
            "exit_modes": list(EXIT_MODES),
            "equity_sources": list(EQUITY_SOURCES),
            "settings_path": str(settings_store.default_path()),
        }

    # --- settings -------------------------------------------------------------

    def _redact_live(cfg: BotConfig) -> dict[str, Any]:
        """Strip exchange credentials from the wire. Keys are write-only via
        the API: a client that echoes the redacted value back has it dropped
        on PUT, so no round trip can leak or wipe the stored secret."""
        out = msgspec.to_builtins(cfg)
        live = out.get("live")
        if isinstance(live, dict):
            if live.get("api_key"):
                live["api_key"] = REDACTED_SECRET
            if live.get("api_secret"):
                live["api_secret"] = REDACTED_SECRET
        return out

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return {
            "app": msgspec.to_builtins(app.state.source.cfg),
            "bot": _redact_live(app.state.source.bot_cfg),
        }

    @app.put("/api/settings")
    async def put_settings(request: Request) -> dict[str, Any]:
        patch = await _decode(request, SettingsPatch)
        if patch is None:
            return _result(False, "settings unchanged", ["request body is not valid JSON"])
        s = app.state.source
        problems: list[str] = []
        new_app: AppConfig | None = None
        new_bot: BotConfig | None = None
        if patch.app is not None:
            current = msgspec.to_builtins(s.cfg)
            problems.extend(f"app: unknown field {k!r}"
                            for k in _unknown_keys(current, patch.app, ""))
            try:
                new_app = msgspec.convert(_deep_merge(current, patch.app), AppConfig)
            except msgspec.ValidationError as exc:
                problems.append(f"app: {exc}")
        if patch.bot is not None:
            live_patch = patch.bot.get("live")
            if isinstance(live_patch, dict):
                # A redacted secret echoed back means "unchanged": drop it so
                # the merge can never overwrite the stored credential.
                for field in ("api_key", "api_secret"):
                    if live_patch.get(field) == REDACTED_SECRET:
                        del live_patch[field]
            current = msgspec.to_builtins(s.bot_cfg)
            problems.extend(f"bot: unknown field {k!r}"
                            for k in _unknown_keys(current, patch.bot, ""))
            try:
                new_bot = msgspec.convert(_deep_merge(current, patch.bot), BotConfig)
            except msgspec.ValidationError as exc:
                problems.append(f"bot: {exc}")
        if problems:
            return _result(False, "settings unchanged", problems)

        # Both halves are validated BEFORE either is applied: a bad bot config
        # must not leave a half-applied app config behind.
        pending: list[tuple[str, Any]] = []
        if new_app is not None:
            problems.extend(f"app: {p}" for p in validate_app(new_app))
            pending.append(("app", new_app))
        if new_bot is not None:
            problems.extend(f"bot: {p}" for p in validate_bot(new_bot))
            pending.append(("bot", new_bot))
        if problems:
            return _result(False, "settings unchanged", problems)

        notes: list[str] = []
        for half, cfg in pending:
            applied, persist_error = (
                s.apply_app(cfg) if half == "app" else s.apply_bot(cfg)
            )
            problems.extend(f"{half}: {p}" for p in applied)
            if half == "bot":
                notes.extend(f"bot warning: {w}" for w in warnings_bot(cfg))
            if persist_error:
                notes.append(persist_error)
        if problems:
            return _result(False, "settings partially applied", problems)
        message = "settings saved" if not notes else f"settings applied ({'; '.join(notes)})"
        return _result(True, message)

    # --- symbols / watchlist ---------------------------------------------------

    @app.get("/api/symbols")
    async def symbols(q: str = "", limit: int = 20) -> list[dict[str, Any]]:
        s = app.state.source
        return [
            {
                "symbol": i.symbol, "name": i.name, "asset_class": i.asset_class,
                "venue": i.venue, "ticker": i.ticker, "exchange": i.exchange,
                "base": i.base, "quote": i.quote, "watched": s.is_watched(i.symbol),
            }
            for i in s.search_symbols(q, limit=limit)
        ]

    @app.get("/api/watchlist")
    async def get_watchlist() -> list[dict[str, Any]]:
        return [
            {"symbol": i.symbol, "name": i.name,
             "asset_class": i.asset_class, "venue": i.venue,
             "ticker": i.ticker, "exchange": i.exchange,
             "base": i.base, "quote": i.quote}
            for i in app.state.source.watched()
        ]

    @app.post("/api/watchlist")
    async def add_watchlist(request: Request) -> dict[str, Any]:
        req = await _decode(request, SymbolRequest)
        if req is None or not req.symbol:
            return _result(False, "a symbol is required")
        ok, message = app.state.source.add_watch(req.symbol)
        return _result(ok, message)

    @app.delete("/api/watchlist/{symbol}")
    async def remove_watchlist(symbol: str) -> dict[str, Any]:
        # Crypto canonicals carry a ':' (binance-spot:BTCUSDT); a plain path
        # param handles that (and its %3A-encoded form) without a converter.
        ok, message = app.state.source.remove_watch(symbol)
        return _result(ok, message)

    @app.post("/api/focus")
    async def set_focus(request: Request) -> dict[str, Any]:
        req = await _decode(request, SymbolRequest)
        if req is None or not req.symbol:
            return _result(False, "a symbol is required")
        s = app.state.source
        s.set_focus(req.symbol)
        return _result(True, f"focus {s.focus}")

    # --- bot -------------------------------------------------------------------

    @app.post("/api/bot/start")
    async def bot_start() -> dict[str, Any]:
        return _from_tuple(await app.state.source.start_bot())

    @app.post("/api/bot/stop")
    async def bot_stop() -> dict[str, Any]:
        return _from_tuple(app.state.source.stop_bot())

    @app.post("/api/bot/pause")
    async def bot_pause() -> dict[str, Any]:
        return _from_tuple(app.state.source.set_bot_paused(True))

    @app.post("/api/bot/resume")
    async def bot_resume() -> dict[str, Any]:
        return _from_tuple(app.state.source.set_bot_paused(False))

    @app.post("/api/bot/halt")
    async def bot_halt() -> dict[str, Any]:
        return _from_tuple(app.state.source.halt_bot())

    # --- command bar -----------------------------------------------------------

    from entropy_sidecar.commands import apply_command

    @app.post("/api/command")
    async def command(request: Request) -> dict[str, Any]:
        req = await _decode(request, CommandRequest)
        if req is None:
            return _result(False, "request body is not a valid command")
        res: CommandResult = apply_command(
            app.state.source, f"{req.verb} {req.arg}".strip()
        )
        return _result(res.ok, res.message, list(res.problems))

    return app
