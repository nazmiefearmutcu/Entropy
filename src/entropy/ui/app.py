from __future__ import annotations

import time
from typing import Any

from crypcodile.schema.records import Trade
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from entropy.app import AppConfig
from entropy.engine.candles import CandleAggregator
from entropy.engine.engine import Engine
from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import start_feed
from entropy.feeds.equities.feed import EquitySimFeed
from entropy.feeds.equities.universe import INDICES
from entropy.strategy.engine import Strategy, StrategyConfig

from .theme import ENTROPY_THEME
from .widgets.boards import refresh_board
from .widgets.charts import Candle, PriceChart, VolumeChart
from .widgets.console import AlgoConsole
from .widgets.gauges import GaugeBar
from .widgets.header import HeaderBar
from .widgets.status_bar import StatusBar, format_telemetry

_S = 1_000_000_000
_CANDLE_INTERVAL_NS = _S  # 1s rolling candles for the live charts


class EntropyApp(App[None]):
    CSS_PATH = "entropy.tcss"
    BINDINGS = [
        ("s", "settings", "Settings"),
        ("question_mark", "help", "Help"),
        ("h", "help", "Help"),
        ("e", "errors", "Errors"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self, config: AppConfig | None = None, *args: Any, headless: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.cfg = config or AppConfig()
        self._sink = QueueSink()
        self.engine = Engine(self.cfg.engine)
        self.strategy = Strategy(StrategyConfig(symbol=self.cfg.strategy_symbol))
        self._equity = EquitySimFeed(
            self._sink, seed=self.cfg.seed, ticks_per_sec=self.cfg.equity_tps
        )
        self._price_candles = CandleAggregator(_CANDLE_INTERVAL_NS)
        self._vol_candles = self._price_candles
        self._spikes = 0
        self._snap_drops = 0

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Horizontal(id="body"):
            yield AlgoConsole(id="console")
            with Vertical(id="center"):
                yield Static("", id="ticker")
                yield GaugeBar(id="gauges")
                yield Static("", id="hist")
                with Horizontal(id="boards"):
                    yield DataTable(id="new_lows")
                    yield DataTable(id="session_highs")
            with Vertical(id="charts"):
                yield PriceChart(id="price")
                yield VolumeChart(id="volume")
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        self.register_theme(ENTROPY_THEME)
        self.theme = "entropy"
        for tid in ("new_lows", "session_highs"):
            t = self.query_one("#" + tid, DataTable)
            t.add_columns("Symbol", "Count", "Price", "%Chg")
            t.cursor_type = "none"
            t.zebra_stripes = False
        self.set_interval(1 / 10, self.sample_snapshot)
        self.run_drain()
        if self.cfg.enable_equities:
            self._run_equity_feed()
        if self.cfg.enable_crypto:
            self._run_crypto_feed()

    def sample_snapshot(self) -> None:
        snap = self.engine.snapshot()
        status = self.query_one("#status", StatusBar)
        status.telemetry = format_telemetry(
            raw_hz=snap.breadth.raw_hz, prev30s=snap.breadth.prev30s_rate,
            snap_drops=self._snap_drops, spikes=self._spikes, accel=snap.breadth.accel,
            dropped=self._sink.dropped,
        )
        status.sell_pct = snap.breadth.sell_pct
        self.query_one("#gauges", GaugeBar).value = snap.breadth.sell_pct / 100.0
        refresh_board(self.query_one("#new_lows", DataTable), snap.new_lows)
        refresh_board(self.query_one("#session_highs", DataTable), snap.new_highs)
        bars = self._price_candles.bars()
        self.query_one("#price", PriceChart).candles = [
            Candle(t=b.t, o=b.o, h=b.h, l=b.l, c=b.c) for b in bars
        ]
        self.query_one("#volume", VolumeChart).bars = [(b.t, b.vol) for b in bars]
        self._update_header(snap)

    def _update_header(self, snap: Any) -> None:
        header = self.query_one("#header", HeaderBar)
        header.clock = time.strftime("%H:%M:%S")
        by_sym: dict[str, Any] = {}
        for board in (snap.top_movers, snap.new_highs, snap.new_lows):
            for r in board:
                by_sym.setdefault(r.symbol, r)
        parts = []
        for sym in INDICES:
            r = by_sym.get(sym)
            if r is not None:
                parts.append(f"{sym} {r.price:.2f} ({r.pct_chg:+.2f}%)")
        header.quotes = "   ".join(parts)

    @work(exclusive=True, group="drain")
    async def run_drain(self) -> None:
        q = self._sink.q
        while True:
            r = await q.get()
            if isinstance(r, Trade):
                evs = self.engine.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)
                for e in evs:
                    kn = type(e).__name__
                    if kn == "Spike":
                        self._spikes += 1
                    elif kn == "SnapDrop":
                        self._snap_drops += 1
                if r.symbol in (self.cfg.strategy_symbol, self.cfg.crypto_strategy_symbol):
                    self._on_strategy(r)
                self._route_candle(r)

    def _on_strategy(self, r: Trade) -> None:
        sevs = self.strategy.on_price(r.symbol, r.price, r.local_ts)
        if not sevs:
            return
        console = self.query_one("#console", AlgoConsole)
        for se in sevs:
            console.push_event(se)

    def _route_candle(self, r: Trade) -> None:
        if r.symbol == self.cfg.strategy_symbol:
            self._price_candles.add(r.local_ts, r.price, r.amount)

    @work(group="feeds")
    async def _run_equity_feed(self) -> None:
        await self._equity.run()

    @work(group="feeds")
    async def _run_crypto_feed(self) -> None:
        task = await start_feed(self._sink)
        await task

    def action_settings(self) -> None: ...
    def action_help(self) -> None: ...
    def action_errors(self) -> None: ...
