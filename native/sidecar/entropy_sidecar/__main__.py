from __future__ import annotations

import secrets
import socket

import uvicorn

from entropy_sidecar.app import create_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> None:
    port = _free_port()
    # Per-process auth token (review H6): the IPC surface can start/stop the
    # bot and write settings, so an unauthenticated 127.0.0.1 API is one
    # drive-by POST away from bot control. Tauri reads TOKEN= off stdout
    # (printed before PORT=, which is the line it breaks on) and injects it
    # into the webview next to the port.
    token = secrets.token_urlsafe(24)
    print(f"TOKEN={token}", flush=True)  # Tauri reads this line from stdout
    print(f"PORT={port}", flush=True)    # Tauri reads this line from stdout
    uvicorn.run(create_app(auth_token=token), host="127.0.0.1", port=port,
                log_level="warning")


if __name__ == "__main__":
    main()
