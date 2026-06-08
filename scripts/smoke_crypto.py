import asyncio
from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import start_feed
from crypcodile.schema.records import Trade

async def main():
    sink = QueueSink(maxsize=10_000)
    task = await start_feed(sink)
    seen = 0
    while seen < 20:
        rec = await sink.q.get()
        if isinstance(rec, Trade):
            print(rec.symbol, rec.price, rec.side.value); seen += 1
    task.cancel()
asyncio.run(main())
