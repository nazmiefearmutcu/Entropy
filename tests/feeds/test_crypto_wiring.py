from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import build_live


def test_build_live_sets_transport():
    from crypcodile.instruments.registry import InstrumentRegistry
    sink = QueueSink()
    reg = InstrumentRegistry()
    c = build_live("coinbase", ["BTC-USD"], ["trade"], sink, reg)
    assert c.transport is not None          # the load-bearing fix
    assert c.ws_url.startswith("wss://")
