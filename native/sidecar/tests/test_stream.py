import pytest
from entropy_sidecar.stream import SnapshotSource
from entropy_sidecar.contract import SnapshotMessage


@pytest.mark.asyncio
async def test_build_snapshot_from_seeded_engine():
    async def _none(_s): return None
    src = SnapshotSource(depth_fetcher=_none)   # inject: no network
    src.engine.on_trade("AAPL", 100.0, 1.0, "buy", 0)
    src.engine.on_trade("AAPL", 110.0, 1.0, "buy", 1_000_000_000)
    src.set_focus("AAPL")
    msg = await src.build()
    assert isinstance(msg, SnapshotMessage)
    assert msg.schema_version == 1
    assert msg.focus.symbol == "AAPL"
    assert msg.focus.last == 110.0
    assert any(row[0] == "AAPL" for row in msg.new_highs)
