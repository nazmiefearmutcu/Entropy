from fastapi.testclient import TestClient
from entropy_sidecar.app import create_app


def test_health_ok():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ws_live_streams_one_snapshot():
    from entropy_sidecar.app import create_app
    from entropy_sidecar.stream import SnapshotSource
    async def no_depth(s): return None
    src = SnapshotSource(depth_fetcher=no_depth)
    src.engine.on_trade("AAPL", 100.0, 1.0, "buy", 0)
    src.set_focus("AAPL")
    client = TestClient(create_app(source=src, tick_hz=50))
    with client.websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "snapshot"
    assert msg["focus"]["symbol"] == "AAPL"
