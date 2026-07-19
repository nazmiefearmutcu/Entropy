import msgspec
from entropy_sidecar.contract import SnapshotMessage, FocusView, DepthLevels, CommandRequest

SCHEMA_VERSION = 1


def test_snapshot_roundtrips_json():
    msg = SnapshotMessage(
        schema_version=SCHEMA_VERSION, ts_ns=1,
        buy_pct=55.0, sell_pct=45.0, raw_hz=640.0, accel="steady",
        new_highs=[("AAPL", 122, 228.6, 1.9)], new_lows=[("DKNG", 28, 39.2, -2.1)],
        ticker=[("15m", [("AAPL", 12)])],
        focus=FocusView(
            symbol="AAPL", asset="EQUITY", last=228.6, pct=0.9, hi=229.4, lo=226.1,
            candles=[(1, 228.0, 229.0, 227.5, 228.6, 1000.0)],
            depth=DepthLevels(basis="yahoo_1m_vap", is_synthetic=True,
                              reference_price=228.66, bids=[(228.6, 2400.0)], asks=[(228.7, 168.0)]),
            fundamentals=None,
        ),
        watchlist=[("NVDA", 121.4, 2.1, [1.0, 2.0, 3.0])],
        market_status="open", source="live",
    )
    raw = msgspec.json.encode(msg)
    back = msgspec.json.decode(raw, type=SnapshotMessage)
    assert back == msg
    assert back.focus.depth.is_synthetic is True


def test_command_request_decodes():
    req = msgspec.json.decode(b'{"verb":"chart","arg":"AAPL"}', type=CommandRequest)
    assert (req.verb, req.arg) == ("chart", "AAPL")
