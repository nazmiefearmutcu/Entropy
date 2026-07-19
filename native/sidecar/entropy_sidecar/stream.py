from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from entropy.engine.engine import Engine
from entropy.ui.widgets.depth_panel import fetch_depth

from entropy_sidecar.contract import DepthLevels, FocusView, SnapshotMessage

SCHEMA_VERSION = 1
DepthFetcher = Callable[[str], Awaitable[Any]]


class SnapshotSource:
    """Owns a headless Engine and the focus symbol; build() assembles one
    SnapshotMessage. Feeds are wired in by the app layer (later task); tests
    drive engine.on_trade directly. depth_fetcher is injectable so tests avoid I/O."""

    def __init__(self, *, depth_fetcher: DepthFetcher = fetch_depth) -> None:
        self.engine = Engine()
        self._focus = "AAPL"
        self._depth_fetcher = depth_fetcher
        self.market_status = ""
        self.source = "sim"

    def set_focus(self, symbol: str) -> None:
        self._focus = symbol.upper() if ":" not in symbol else symbol

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
            candles=[], depth=depth, fundamentals=None,
        )
