# src/entropy/feeds/equities/feed.py
from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Iterable

from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade
from crypcodile.sink.base import Sink
from crypcodile.util.time import now_ns

from .sim import EquitySimulator
from .universe import UNIVERSE

EXCHANGE = "sim-equity"
_SIDE = {"buy": Side.BUY, "sell": Side.SELL}

class EquitySimFeed:
    def __init__(self, sink: Sink, *, seed: int = 7, ticks_per_sec: int = 4000,
                 clock_ns: Callable[[], int] = now_ns,
                 market_hours_gate: Callable[[int], bool] | None = None,
                 batch_dt: float = 0.01) -> None:
        self.sink = sink
        self.rng = random.Random(seed)
        self.clock_ns = clock_ns
        self.sim = EquitySimulator(self.rng, clock_ns)
        self.tps = ticks_per_sec
        self.batch_dt = batch_dt
        self.gate = market_hours_gate
        self._ids = 0

    def _next_id(self) -> str:
        self._ids += 1
        return f"e{self._ids}"

    def _emit_batch(self, n: int) -> Iterable[Trade]:
        ts = self.clock_ns()
        for _ in range(n):
            sym = self.rng.choice(UNIVERSE)
            s, px, size, side = self.sim.step_symbol(sym)
            yield Trade(exchange=EXCHANGE, symbol=s, symbol_raw=s,
                        exchange_ts=ts, local_ts=ts, id=self._next_id(),
                        price=px, amount=float(size), side=_SIDE[side])

    async def run(self) -> None:
        try:
            while True:
                if self.gate is None or self.gate(self.clock_ns()):
                    self.sim.maybe_inject_events()
                    # Recomputed every batch (cheap) so a hot-applied `tps`
                    # (Settings save) changes the emit rate immediately.
                    per_batch = max(1, int(self.tps * self.batch_dt))
                    for tr in self._emit_batch(per_batch):
                        await self.sink.put(tr)
                await asyncio.sleep(self.batch_dt)
        except asyncio.CancelledError:
            await self.sink.flush()
            raise
