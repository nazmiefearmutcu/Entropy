from __future__ import annotations

import asyncio

import msgspec
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from entropy_sidecar.stream import SnapshotSource

# The sidecar binds 127.0.0.1 only and is consumed solely by the Tauri shell
# (origin tauri://localhost on macOS) and the Vite dev server. Restrict CORS to
# those origins rather than a wildcard (avoids the permissive-CORS smell).
_ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_app(*, source: SnapshotSource | None = None, tick_hz: float = 10.0) -> FastAPI:
    app = FastAPI(title="entropy-sidecar")
    app.add_middleware(
        CORSMiddleware, allow_origins=_ALLOWED_ORIGINS,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.state.source = source or SnapshotSource()
    interval = 1.0 / tick_hz

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, bool]:
        return {"ready": True}

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
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

    from entropy_sidecar.commands import apply_command
    from entropy_sidecar.contract import CommandRequest

    @app.post("/api/command")
    async def command(request: Request) -> dict[str, object]:
        # Decode the raw body with msgspec into CommandRequest — FastAPI can't
        # use a msgspec.Struct as a Pydantic body model, so parse it directly
        # (keeps the contract single-sourced instead of duplicating a schema).
        req = msgspec.json.decode(await request.body(), type=CommandRequest)
        res = apply_command(app.state.source, f"{req.verb} {req.arg}".strip())
        return {"ok": res.ok, "message": res.message}

    return app
