import asyncio

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


@pytest.mark.asyncio
async def test_feeds_populate_engine():
    from entropy.feeds.equities.universe import UNIVERSE

    async def _none(_s): return None
    src = SnapshotSource(depth_fetcher=_none)   # 15m timeframe, sim equity feed
    await src.start_feeds()
    try:
        # let the sim feed emit + drain briefly
        for _ in range(30):
            await asyncio.sleep(0.02)
            if any(src.engine.quote(s) is not None for s in UNIVERSE):
                break
        # the feed -> drain -> engine.on_trade pipeline saw real trades
        assert any(src.engine.quote(s) is not None for s in UNIVERSE)
        msg = await src.build()
        assert isinstance(msg, SnapshotMessage)
    finally:
        await src.stop_feeds()
