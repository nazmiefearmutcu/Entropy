from __future__ import annotations

import time
from typing import Any

import msgspec
from crypcodile.schema.records import Trade
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static
from textual.css.query import NoMatches

from entropy.app import AppConfig
from entropy.config import EngineConfig
from entropy.engine.candles import CandleAggregator
from entropy.engine.engine import Engine
from entropy.engine.timeframe import get_timeframe
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
        self._tf = get_timeframe(self.cfg.timeframe)
        self._candle_interval_ns = self._tf.bar_ns
        self._warmup_bars = self._tf.warmup_bars
        self._warmup_dt_ns = self._tf.bar_ns
        self._sink = QueueSink()
        self.engine = Engine(
            msgspec.structs.replace(
                self.cfg.engine,
                windows_ns=EngineConfig.from_timeframe(self._tf).windows_ns,
                window_labels=self._tf.window_labels,
                momentum_horizon_s=self._tf.momentum_horizon_s,
                breadth_window_s=self._tf.breadth_window_s,
                momentum_cooldown_ns=self._tf.momentum_cooldown_ns,
            )
        )
        self.strategy = Strategy(StrategyConfig(symbol=self.cfg.strategy_symbol))
        # One Strategy per traded symbol: SPY (sim, clean fees) + BTC (real crypto, ~1bp).
        self.crypto_strategy = Strategy(
            StrategyConfig(symbol=self.cfg.crypto_strategy_symbol, fee_bps=1.0)
        )
        self._equity = EquitySimFeed(
            self._sink, seed=self.cfg.seed, ticks_per_sec=self.cfg.equity_tps
        )
        self._price_candles = CandleAggregator(self._candle_interval_ns)   # SPY (sim)
        self._crypto_candles = CandleAggregator(self._candle_interval_ns)  # BTC (live crypto)
        self._spikes = 0
        self._snap_drops = 0
        self._error_text = "No errors."

    def query_default(self, selector: str, expect_type: Any) -> Any:
        try:
            return self.get_screen("_default").query_one(selector, expect_type)
        except Exception:
            return self.query_one(selector, expect_type)

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
        from entropy.bot.ledger import init_trade_csv
        init_trade_csv(self.cfg.trade_csv_path)

        from .theme import ALL_THEMES
        for th in ALL_THEMES:
            self.register_theme(th)
        self.theme = self.cfg.theme
        
        # Apply initial settings
        self.query_one("#price", PriceChart).chart_type = self.cfg.chart_type
        self.query_one("#price2", PriceChart).chart_type = self.cfg.chart_type
        self.query_one("#volume", VolumeChart).display = self.cfg.show_volume
        self.query_one("#volume2", VolumeChart).display = self.cfg.show_volume
        self.query_one("#hist", HighLowGauges).window_labels = self.engine.cfg.window_labels

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
        try:
            snap = self.engine.snapshot()
            status = self.query_default("#status", StatusBar)
            status.telemetry = format_telemetry(
                raw_hz=snap.breadth.raw_hz, prev=snap.breadth.prev30s_rate,
                snap_drops=self._snap_drops, spikes=self._spikes, accel=snap.breadth.accel,
                dropped=self._sink.dropped,
            )
            status.sell_pct = snap.breadth.sell_pct
            self.query_default("#ticker", TickerStrip).groups = snap.ticker
            self.query_default("#event_hist", EventHistogram).raw_hz = snap.breadth.raw_hz
            hist = self.query_default("#hist", HighLowGauges)
            hist.nh_counts = snap.breadth.nh_counts
            hist.nl_counts = snap.breadth.nl_counts
            refresh_board(self.query_default("#new_lows", DataTable), snap.new_lows, self)
            refresh_board(self.query_default("#session_highs", DataTable), snap.new_highs, self)
            self._draw_chart("#price", "#volume", self._crypto_candles)   # BTC (live)
            self._draw_chart("#price2", "#volume2", self._price_candles)  # SPY (sim)
            self._update_header()
        except NoMatches:
            pass

    def _draw_chart(self, price_id: str, vol_id: str, agg: CandleAggregator) -> None:
        bars = agg.bars()
        try:
            self.query_default(price_id, PriceChart).candles = [
                Candle(t=b.t, o=b.o, h=b.h, l=b.l, c=b.c) for b in bars
            ]
            self.query_default(vol_id, VolumeChart).bars = [(b.t, b.vol) for b in bars]
        except NoMatches:
            pass

    def _update_header(self) -> None:
        try:
            header = self.query_default("#header", HeaderBar)
            header.clock = time.strftime("%H:%M:%S")
            parts = []
            for sym in INDICES:
                q = self.engine.quote(sym)   # always-on index quotes (too calm for boards)
                if q is not None:
                    price, pct = q
                    parts.append(f"{sym} {price:.2f} ({pct:+.2f}%)")
            header.quotes = "   ".join(parts)
        except NoMatches:
            pass

    def _push_info(self, text: str, color: str = "white") -> None:
        try:
            self.query_default("#console", AlgoConsole).push_info(text, color)
        except NoMatches:
            pass

    def _push_events(self, events: list[StrategyEvent]) -> None:
        try:
            console = self.query_default("#console", AlgoConsole)
            for e in events:
                console.push_event(e)
        except NoMatches:
            pass

    def _synth_spy_bars(self) -> list[Bar]:
        """Synthesize the strategy's warmup tail from the sim's current SPY price."""
        sym = self.cfg.strategy_symbol
        rt = self._equity.sim.rt.get(sym)
        px = rt.px if rt is not None else 100.0
        now = self._equity.clock_ns()
        return [
            Bar(ts_ns=now - (self._warmup_bars - 1 - i) * self._warmup_dt_ns, close=px)
            for i in range(self._warmup_bars)
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

        from entropy.bot.ledger import record_trade_open, record_trade_close
        for se in sevs:
            if se.kind is EventKind.OPEN_LONG:
                record_trade_open(self.cfg.trade_csv_path, se.symbol, "LONG", se.price)
            elif se.kind is EventKind.OPEN_SHORT:
                record_trade_open(self.cfg.trade_csv_path, se.symbol, "SHORT", se.price)
            elif se.kind is EventKind.CLOSE_LONG:
                record_trade_close(self.cfg.trade_csv_path, se.symbol, "LONG", se.price)
            elif se.kind is EventKind.CLOSE_SHORT:
                record_trade_close(self.cfg.trade_csv_path, se.symbol, "SHORT", se.price)

        try:
            console = self.query_default("#console", AlgoConsole)
            for se in sevs:
                console.push_event(se)
        except NoMatches:
            pass

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
        try:
            header = self.query_default("#header", HeaderBar)
        except Exception:
            header = None
        try:
            task = await start_feed(self._sink)
            if header is not None:
                header.sources = "coinbase ●  binance ●"   # feed live
            await task
            if header is not None:
                header.sources = "coinbase ○  binance ○"   # task ended cleanly
        except Exception as exc:
            if header is not None:
                header.sources = "coinbase ○  binance ○"   # disconnected
            self._feed_status(f"disconnect: {exc}", "red")
            self._error_text = f"crypto feed: {exc}"

    def action_help(self) -> None:
        self.push_screen(HelpScreen(id="help"))

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen(id="settings"))

    def action_errors(self) -> None:
        self.push_screen(ErrorScreen(self._error_text, id="errors"))

    def _apply_settings(
        self, *, theme: str, chart_type: str, show_volume: bool, timeframe: str,
        enable_equities: bool, enable_crypto: bool, equity_tps: int,
        strategy_symbol: str, crypto_strategy_symbol: str,
        spike_pct: float, snapdrop_pct: float,
    ) -> None:
        tf_changed = timeframe != self.cfg.timeframe
        spec = get_timeframe(timeframe)
        # Overlay timeframe-derived windows/scalars + form spike/snapdrop onto the existing
        # engine config so non-form fields (upmove/downmove/leaderboard_k/accel_eps) are preserved.
        new_engine_cfg = msgspec.structs.replace(
            self.cfg.engine,
            windows_ns=EngineConfig.from_timeframe(spec).windows_ns,
            window_labels=spec.window_labels,
            momentum_horizon_s=spec.momentum_horizon_s,
            breadth_window_s=spec.breadth_window_s,
            momentum_cooldown_ns=spec.momentum_cooldown_ns,
            spike_pct=spike_pct,
            snapdrop_pct=snapdrop_pct,
        )
        self.cfg = msgspec.structs.replace(
            self.cfg, theme=theme, chart_type=chart_type, show_volume=show_volume,
            timeframe=timeframe, enable_equities=enable_equities, enable_crypto=enable_crypto,
            equity_tps=equity_tps, strategy_symbol=strategy_symbol,
            crypto_strategy_symbol=crypto_strategy_symbol, engine=new_engine_cfg,
        )
        self.theme = theme
        self.query_default("#price", PriceChart).chart_type = chart_type
        self.query_default("#price2", PriceChart).chart_type = chart_type
        self.query_default("#volume", VolumeChart).display = show_volume
        self.query_default("#volume2", VolumeChart).display = show_volume
        if self._equity is not None:
            self._equity.tps = equity_tps

        # Rebuild strategies for changed symbols FIRST (no warmup yet) so the timeframe-change
        # warmup below runs exactly once against the new strategy objects.
        strat_symbol_changed = self.strategy.cfg.symbol != strategy_symbol
        crypto_symbol_changed = self.crypto_strategy.cfg.symbol != crypto_strategy_symbol
        if strat_symbol_changed:
            self.strategy = Strategy(StrategyConfig(symbol=strategy_symbol))
        if crypto_symbol_changed:
            self.crypto_strategy = Strategy(StrategyConfig(symbol=crypto_strategy_symbol, fee_bps=1.0))

        if tf_changed:
            self._tf = spec
            self.engine = Engine(new_engine_cfg)
            self._candle_interval_ns = spec.bar_ns
            self._warmup_bars = spec.warmup_bars
            self._warmup_dt_ns = spec.bar_ns
            self._price_candles = CandleAggregator(spec.bar_ns)
            self._crypto_candles = CandleAggregator(spec.bar_ns)
            self.query_default("#hist", HighLowGauges).window_labels = spec.window_labels
            self._warmup_strategies()  # warms equity + (if enabled) crypto once, with new symbols
        else:
            self.engine.cfg = new_engine_cfg
            if strat_symbol_changed:
                self._push_events(self.strategy.warmup(self._synth_spy_bars()))
                self._push_info(f"watching [{strategy_symbol}]")
            if crypto_symbol_changed:
                self._warmup_crypto()
