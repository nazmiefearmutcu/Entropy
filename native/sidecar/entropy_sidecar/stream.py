from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from crypcodile.schema.records import Trade

from entropy.config import EngineConfig
from entropy.engine.candles import CandleAggregator
from entropy.engine.engine import Engine
from entropy.engine.timeframe import get_timeframe
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed
from entropy.ui.widgets.depth_panel import fetch_depth
from entropy_sidecar.contract import DepthLevels, FocusView, SnapshotMessage

SCHEMA_VERSION = 1
DepthFetcher = Callable[[str], Awaitable[Any]]


class SnapshotSource:
    """Owns a headless Engine + the equity sim feed and the focus symbol;
    ``build()`` assembles one SnapshotMessage. ``start_feeds()`` spawns the
    background feed + drain tasks (the app's lifespan calls it); tests can drive
    ``engine.on_trade`` directly without starting feeds. depth_fetcher is
    injectable so tests avoid network I/O.

    The engine is built on the 15-minute timeframe (matching the TUI) rather
    than a bare ``Engine()`` (which is the legacy second-scale default).
    """

    def __init__(
        self,
        *,
        depth_fetcher: DepthFetcher = fetch_depth,
        timeframe: str = "15m",
        seed: int = 7,
        ticks_per_sec: int = 4000,
    ) -> None:
        tf = get_timeframe(timeframe)
        self.engine = Engine(EngineConfig.from_timeframe(tf))
        self._focus = "AAPL"
        self._depth_fetcher = depth_fetcher
        self.market_status = ""
        self.source = "sim"
        self._sink = QueueSink()
        self._feed = EquitySimFeed(self._sink, seed=seed, ticks_per_sec=ticks_per_sec)
        self._candle_interval_ns = tf.bar_ns
        self._focus_candles = CandleAggregator(self._candle_interval_ns)
        self._tasks: list[asyncio.Task[None]] = []

    def set_focus(self, symbol: str) -> None:
        new = symbol.upper() if ":" not in symbol else symbol
        if new != self._focus:
            self._focus = new
            # Reset the chart aggregator so it tracks the newly-focused symbol.
            self._focus_candles = CandleAggregator(self._candle_interval_ns)

    async def start_feeds(self) -> None:
        """Spawn the equity sim feed + drain loop (idempotent)."""
        if self._tasks:
            return
        self._tasks.append(asyncio.create_task(self._feed.run()))
        self._tasks.append(asyncio.create_task(self._drain()))

    async def stop_feeds(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    async def _drain(self) -> None:
        q = self._sink.q
        drained = 0
        while True:
            r = await q.get()
            if isinstance(r, Trade):
                self.engine.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)
                if r.symbol == self._focus:
                    self._focus_candles.add(r.local_ts, r.price, r.amount)
            # Under sustained load q.get() never suspends; hand back periodically
            # so the WS send loop isn't starved.
            drained += 1
            if drained % 200 == 0:
                await asyncio.sleep(0)

    async def build(self) -> SnapshotMessage:
        snap = self.engine.snapshot()
        leaders = lambda rows: [(r.symbol, r.count, r.price, r.pct_chg) for r in rows]  # noqa: E731
        ticker = [(g.window, [(s, c) for s, c in g.entries]) for g in snap.ticker]
        focus = await self._build_focus(self._focus)
        return SnapshotMessage(
            schema_version=SCHEMA_VERSION, ts_ns=snap.ts_ns,
            buy_pct=snap.breadth.buy_pct, sell_pct=snap.breadth.sell_pct,
            raw_hz=snap.breadth.raw_hz, accel=snap.breadth.accel,
            new_highs=leaders(snap.new_highs), new_lows=leaders(snap.new_lows),
            ticker=ticker, focus=focus, watchlist=[],
            market_status=self.market_status, source=self.source,
        )

    async def _build_focus(self, symbol: str) -> FocusView:
        quote = self.engine.quote(symbol)
        rng = self.engine.session_range(symbol)
        asset = "CRYPTO" if ":" in symbol else ("EQUITY" if self.source == "live" else "SIM")
        candles = [(b.t, b.o, b.h, b.l, b.c, b.vol) for b in self._focus_candles.bars()]
        depth = None
        if asset == "EQUITY":
            try:
                view = await self._depth_fetcher(symbol)
            except Exception:
                view = None
            if view is not None:
                depth = DepthLevels(
                    basis=view.basis, is_synthetic=view.is_synthetic,
                    reference_price=view.reference_price
                    if view.reference_price is not None else 0.0,
                    bids=[(p, s) for p, s in view.bids],
                    asks=[(p, s) for p, s in view.asks],
                )
        return FocusView(
            symbol=symbol, asset=asset,
            last=quote[0] if quote else None, pct=quote[1] if quote else None,
            hi=rng[0] if rng else None, lo=rng[1] if rng else None,
            candles=candles, depth=depth, fundamentals=None,
        )
