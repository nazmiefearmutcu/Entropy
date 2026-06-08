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
from entropy.feeds.warmup import warmup_klines
from entropy.strategy.engine import Bar, EventKind, Strategy, StrategyConfig, StrategyEvent

from .theme import ENTROPY_THEME
from .widgets.boards import refresh_board
from .widgets.charts import Candle, PriceChart, VolumeChart
from .widgets.console import AlgoConsole
from .widgets.header import HeaderBar
from .widgets.highlow_gauges import HighLowGauges
from .widgets.histogram import EventHistogram
from .widgets.modals import ErrorScreen, HelpScreen, SettingsScreen
from .widgets.status_bar import StatusBar, format_telemetry
from .widgets.ticker_strip import TickerStrip

_S = 1_000_000_000
_CANDLE_INTERVAL_NS = _S  # 1s rolling candles for the live charts
_WARMUP_BARS = 24  # matches the GIF's "warmup: 24 bars" line
_WARMUP_DT_NS = 60 * _S  # 1-minute synthetic warmup cadence


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
        # One Strategy per traded symbol: SPY (sim, clean fees) + BTC (real crypto, ~1bp).
        self.crypto_strategy = Strategy(
            StrategyConfig(symbol=self.cfg.crypto_strategy_symbol, fee_bps=1.0)
        )
        self._equity = EquitySimFeed(
            self._sink, seed=self.cfg.seed, ticks_per_sec=self.cfg.equity_tps
        )
        self._price_candles = CandleAggregator(_CANDLE_INTERVAL_NS)   # SPY (sim)
        self._crypto_candles = CandleAggregator(_CANDLE_INTERVAL_NS)  # BTC (live crypto)
        self._spikes = 0
        self._snap_drops = 0
        self._error_text = "No errors."

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Horizontal(id="body"):
            yield AlgoConsole(id="console")
            with Vertical(id="center"):
                yield TickerStrip(id="ticker")
                yield HighLowGauges(id="hist")
                yield EventHistogram(id="event_hist")
                with Horizontal(id="boards"):
                    with Vertical(classes="board-col"):
                        yield Static("On new lows", classes="board-title")
                        yield DataTable(id="new_lows")
                    with Vertical(classes="board-col"):
                        yield Static("Session new highs", classes="board-title")
                        yield DataTable(id="session_highs")
            with Vertical(id="charts"):
                yield PriceChart(id="price")        # BTC (live crypto)
                yield VolumeChart(id="volume")
                yield PriceChart(id="price2")       # SPY (sim)
                yield VolumeChart(id="volume2")
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
        self._warmup_strategies()
        self.run_drain()
        if self.cfg.enable_equities:
            self._run_equity_feed()
        if self.cfg.enable_crypto:
            self._run_crypto_feed()
        else:
            self.query_one("#header", HeaderBar).sources = "equities only"

    def sample_snapshot(self) -> None:
        snap = self.engine.snapshot()
        status = self.query_one("#status", StatusBar)
        status.telemetry = format_telemetry(
            raw_hz=snap.breadth.raw_hz, prev30s=snap.breadth.prev30s_rate,
            snap_drops=self._snap_drops, spikes=self._spikes, accel=snap.breadth.accel,
            dropped=self._sink.dropped,
        )
        status.sell_pct = snap.breadth.sell_pct
        self.query_one("#ticker", TickerStrip).groups = snap.ticker
        self.query_one("#event_hist", EventHistogram).raw_hz = snap.breadth.raw_hz
        hist = self.query_one("#hist", HighLowGauges)
        hist.nh_counts = snap.breadth.nh_counts
        hist.nl_counts = snap.breadth.nl_counts
        refresh_board(self.query_one("#new_lows", DataTable), snap.new_lows)
        refresh_board(self.query_one("#session_highs", DataTable), snap.new_highs)
        self._draw_chart("#price", "#volume", self._crypto_candles)   # BTC (live)
        self._draw_chart("#price2", "#volume2", self._price_candles)  # SPY (sim)
        self._update_header()

    def _draw_chart(self, price_id: str, vol_id: str, agg: CandleAggregator) -> None:
        bars = agg.bars()
        self.query_one(price_id, PriceChart).candles = [
            Candle(t=b.t, o=b.o, h=b.h, l=b.l, c=b.c) for b in bars
        ]
        self.query_one(vol_id, VolumeChart).bars = [(b.t, b.vol) for b in bars]

    def _update_header(self) -> None:
        header = self.query_one("#header", HeaderBar)
        header.clock = time.strftime("%H:%M:%S")
        parts = []
        for sym in INDICES:
            q = self.engine.quote(sym)   # always-on index quotes (too calm for boards)
            if q is not None:
                price, pct = q
                parts.append(f"{sym} {price:.2f} ({pct:+.2f}%)")
        header.quotes = "   ".join(parts)

    def _push_info(self, text: str, color: str = "white") -> None:
        self.query_one("#console", AlgoConsole).push_info(text, color)

    def _push_events(self, events: list[StrategyEvent]) -> None:
        console = self.query_one("#console", AlgoConsole)
        for e in events:
            console.push_event(e)

    def _synth_spy_bars(self) -> list[Bar]:
        """Synthesize the strategy's warmup tail from the sim's current SPY price."""
        sym = self.cfg.strategy_symbol
        rt = self._equity.sim.rt.get(sym)
        px = rt.px if rt is not None else 100.0
        now = self._equity.clock_ns()
        return [
            Bar(ts_ns=now - (_WARMUP_BARS - 1 - i) * _WARMUP_DT_NS, close=px)
            for i in range(_WARMUP_BARS)
        ]

    def _warmup_strategies(self) -> None:
        # SPY warms instantly from synthesized sim bars; push its INFO + watching line.
        self._push_events(self.strategy.warmup(self._synth_spy_bars()))
        self._push_info(f"watching [{self.cfg.strategy_symbol}]")
        if self.cfg.enable_crypto:
            self._warmup_crypto()

    @work(group="warmup")
    async def _warmup_crypto(self) -> None:
        raw = self.cfg.crypto_strategy_symbol.split(":", 1)[-1]
        try:
            bars = await warmup_klines(raw)
        except Exception as exc:  # network/REST hiccup — warmup is best-effort.
            self._error_text = f"crypto warmup failed: {exc}"
            return
        if not bars:
            return
        events = self.crypto_strategy.warmup(bars)
        info = [e for e in events if e.kind is EventKind.INFO]
        self._push_events(info)

    def _feed_status(self, text: str, color: str = "white") -> None:
        """Surface transport connect/disconnect noise as console INFO lines."""
        self._push_info(text, color)

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
        strat = (
            self.crypto_strategy
            if r.symbol == self.cfg.crypto_strategy_symbol
            else self.strategy
        )
        sevs = strat.on_price(r.symbol, r.price, r.local_ts)
        if not sevs:
            return
        console = self.query_one("#console", AlgoConsole)
        for se in sevs:
            console.push_event(se)

    def _route_candle(self, r: Trade) -> None:
        if r.symbol == self.cfg.strategy_symbol:
            self._price_candles.add(r.local_ts, r.price, r.amount)
        elif r.symbol == self.cfg.crypto_strategy_symbol:
            self._crypto_candles.add(r.local_ts, r.price, r.amount)

    @work(group="feeds")
    async def _run_equity_feed(self) -> None:
        await self._equity.run()

    @work(group="feeds")
    async def _run_crypto_feed(self) -> None:
        # Reconnect noise lives in the console layer (keeps the engine pure).
        self._feed_status("connecting…")
        header = self.query_one("#header", HeaderBar)
        try:
            task = await start_feed(self._sink)
            header.sources = "coinbase ●  binance ●"   # feed live
            await task
            header.sources = "coinbase ○  binance ○"   # task ended cleanly
        except Exception as exc:
            header.sources = "coinbase ○  binance ○"   # disconnected
            self._feed_status(f"disconnect: {exc}", "red")
            self._error_text = f"crypto feed: {exc}"

    def action_help(self) -> None:
        self.push_screen(HelpScreen(id="help"))

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen(id="settings"))

    def action_errors(self) -> None:
        self.push_screen(ErrorScreen(self._error_text, id="errors"))
