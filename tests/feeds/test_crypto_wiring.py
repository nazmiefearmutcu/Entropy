import pytest

from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import _resolve_symbols, build_live


def test_build_live_sets_transport():
    from crypcodile.instruments.registry import InstrumentRegistry
    sink = QueueSink()
    reg = InstrumentRegistry()
    c = build_live("coinbase", ["BTC-USD"], ["trade"], sink, reg)
    assert c.transport is not None          # the load-bearing fix
    assert c.ws_url.startswith("wss://")


@pytest.mark.asyncio
async def test_resolve_symbols_falls_back_when_discovery_fails():
    """A stale/unreachable list_instruments() must degrade to the whitelist,
    not crash the feed (the live WS feed does not depend on discovery)."""
    from crypcodile.instruments.registry import InstrumentRegistry

    class _Broken:
        async def list_instruments(self):
            raise RuntimeError("404 Not Found")

    reg = InstrumentRegistry()
    out = await _resolve_symbols(_Broken(), reg, ["BTC-USD", "ETH-USD"], "USD")
    assert out == ["BTC-USD", "ETH-USD"]
