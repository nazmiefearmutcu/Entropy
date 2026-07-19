from __future__ import annotations

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
    print(f"PORT={port}", flush=True)   # Tauri reads this line from stdout
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
