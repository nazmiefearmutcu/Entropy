from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
from crypcodile.schema.records import Trade
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import DataTable, Input, Static

from entropy.app import AppConfig
from entropy.config import EngineConfig
from entropy.data.universe import SymbolInfo, UniverseService
from entropy.data.watchlist import Watchlist
from entropy.engine.candles import CandleAggregator
from entropy.engine.engine import Engine
from entropy.engine.timeframe import get_timeframe
from entropy.feeds.bus import QueueSink
from entropy.feeds.crypto import start_feed
from entropy.feeds.equities.feed import EquitySimFeed
from entropy.feeds.equities.source import market_status, resolve_equity_source
from entropy.feeds.equities.universe import INDICES, LIVE_UNIVERSE
from entropy.feeds.warmup import warmup_equity_bars, warmup_klines
from entropy.strategy.engine import Bar, EventKind, Strategy, StrategyConfig, StrategyEvent

from .widgets.boards import refresh_board
from .widgets.charts import Candle, ChartRedrawMemo, PriceChart, VolumeChart
from .widgets.command_bar import CommandBar, CommandError, parse_command
from .widgets.console import AlgoConsole
from .widgets.depth_panel import (
    DEPTH_TTL_S,
    DepthPanel,
    DepthView,
    fetch_depth,
)
from .widgets.header import HeaderBar
from .widgets.highlow_gauges import HighLowGauges
from .widgets.histogram import EventHistogram
from .widgets.modals import ErrorScreen, HelpScreen, SettingsScreen
from .widgets.quote_panel import (
    FUNDAMENTALS_TTL_S,
    Fundamentals,
    QuotePanel,
    QuoteState,
    fetch_fundamentals_google,
)
from .widgets.search import SearchScreen
from .widgets.status_bar import StatusBar, format_telemetry
from .widgets.ticker_strip import TickerStrip
from .widgets.watchlist_board import SPARK_WINDOW, WatchlistBoard, WatchRow, sparkline

if TYPE_CHECKING:
    from crypcodile.sink.base import Sink

    from entropy.feeds.equities.live import EquityProviderPlan

log = logging.getLogger(__name__)

_MARKET_STATUS_TTL_S = 30.0  # header chip: recompute the calendar at most this often

# Cached stockodile.analytics module ref for the EMA chart overlays.
# None = not yet tried, False = unavailable (never retried), module otherwise.
_analytics: Any = None


def _ema_module() -> Any | None:
    """Lazy stockodile.analytics import: first chart draw pays it once; a
    missing/broken dep degrades to overlay-free charts, never a crash."""
    global _analytics
    if _analytics is None:
        try:
            from stockodile import analytics
            _analytics = analytics
        except Exception:
            _analytics = False
    return _analytics or None


async def start_equity_feed(
    sink: Sink, symbols: Sequence[str]
) -> tuple[asyncio.Task[None], EquityProviderPlan]:
    """Lazy indirection over the live module: the sim path must never import
    stockodile, and tests monkeypatch this symbol to stub the live feed."""
    from entropy.feeds.equities import live
    return await live.start_equity_feed(sink, symbols)


class EntropyApp(App[None]):
    CSS_PATH = "entropy.tcss"
    BINDINGS = [
        ("s", "settings", "Settings"),
        ("question_mark", "help", "Help"),
        ("h", "help", "Help"),
        ("e", "errors", "Errors"),
        ("slash", "search", "Search"),
        ("w", "watch_toggle", "Watch"),
        ("colon", "command_bar", "Cmd"),
        ("q", "quit", "Quit"),
    ]

    # Symbol driving chart pair #1 (#price/#volume); defaults to the crypto
    # strategy symbol in __init__ (init=False: the watcher only runs on real
    # changes, so boot keeps the legacy warmup/chart behavior).
    focus_symbol: reactive[str] = reactive("", init=False)

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
        # Derive the running engine config from the active timeframe (overlaid onto any
        # caller-supplied engine fields) and write it back into cfg so self.cfg.engine
        # stays the single source of truth (no stale legacy-default divergence).
        engine_cfg = msgspec.structs.replace(
            self.cfg.engine,
            windows_ns=EngineConfig.from_timeframe(self._tf).windows_ns,
            window_labels=self._tf.window_labels,
            momentum_horizon_s=self._tf.momentum_horizon_s,
            breadth_window_s=self._tf.breadth_window_s,
            momentum_cooldown_ns=self._tf.momentum_cooldown_ns,
        )
        self.cfg = msgspec.structs.replace(self.cfg, engine=engine_cfg)
        self.engine = Engine(engine_cfg)
        self.strategy = Strategy(StrategyConfig(symbol=self.cfg.strategy_symbol))
        # One Strategy per traded symbol: SPY (sim, clean fees) + BTC (real crypto, ~1bp).
        self.crypto_strategy = Strategy(
            StrategyConfig(symbol=self.cfg.crypto_strategy_symbol, fee_bps=1.0)
        )
        self._equity = EquitySimFeed(
            self._sink, seed=self.cfg.seed, ticks_per_sec=self.cfg.equity_tps
        )
        self._price_candles = CandleAggregator(self._candle_interval_ns)  # chart #2 (strategy)
        self._focus_candles = CandleAggregator(self._candle_interval_ns)  # chart #1 (focus)
        self._spikes = 0
        self._snap_drops = 0
        self._error_text = "No errors."
        self._market_status_cache = ""
        self._market_status_ts: float | None = None
        # Concrete "sim"/"live" chosen by _run_equity_feed (None until the feed
        # worker resolves it); routes the SPY warmup to real vs synthetic bars.
        self._equity_source_resolved: str | None = None
        watchlist_path = (
            Path(self.cfg.watchlist_path)
            if self.cfg.watchlist_path
            else Path.home() / ".entropy" / "watchlist.json"
        )
        self._watchlist = Watchlist(watchlist_path)
        self._universe = UniverseService()
        # Per-watched-symbol ring buffer of last prices (one sample per
        # snapshot) backing the watchlist board's sparkline column.
        self._watch_prices: dict[str, deque[float]] = {}
        # Per-chart-pair redraw memo: the 10 Hz snapshot skips the full
        # plotext rebuild while a chart's data/settings fingerprint holds.
        self._chart_memo = ChartRedrawMemo()
        # Fundamentals for the quote panel: injectable one-shot fetcher (tests
        # swap in a fake), {symbol: (monotonic_ts, data|None)} TTL cache, an
        # injectable clock, and an in-flight marker so the 10 Hz snapshot
        # doesn't relaunch (and thereby cancel) the exclusive worker.
        self._fundamentals_fetcher: Callable[[str], Awaitable[Fundamentals | None]] = (
            fetch_fundamentals_google
        )
        self._fundamentals_cache: dict[str, tuple[float, Fundamentals | None]] = {}
        self._fundamentals_now: Callable[[], float] = time.monotonic
        self._fundamentals_inflight: str | None = None
        # Market depth for the depth panel: the SAME lazy/TTL/inflight machinery
        # as fundamentals. The injected fetcher is a config-bound partial so the
        # callable stays symbol-only (tests swap it wholesale); the cache holds
        # the last DepthView (or None) per symbol behind DEPTH_TTL_S.
        self._depth_fetcher: Callable[[str], Awaitable[DepthView | None]] = partial(
            fetch_depth, bins=self.cfg.depth_bins, top_n=self.cfg.depth_top_n
        )
        self._depth_cache: dict[str, tuple[float, DepthView | None]] = {}
        self._depth_now: Callable[[], float] = time.monotonic
        self._depth_inflight: str | None = None
        # set_reactive: seed the default without firing the watcher (chart #1
        # starts on the crypto strategy symbol, exactly as before).
        self.set_reactive(EntropyApp.focus_symbol, self.cfg.crypto_strategy_symbol)

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
                    with Vertical(classes="board-col"):
                        yield Static("Watchlist", classes="board-title")
                        yield WatchlistBoard(id="watchlist")
            with Vertical(id="charts"):
                yield PriceChart(id="price")        # chart #1: follows focus_symbol
                yield VolumeChart(id="volume")
                yield QuotePanel(id="quote")        # focus-symbol detail readout
                yield DepthPanel(id="depth")        # DOM ladder (hidden by default)
                yield PriceChart(id="price2")       # chart #2: strategy symbol (SPY)
                yield VolumeChart(id="volume2")
        yield CommandBar(id="cmdbar")               # ":" reveals; hidden by default
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
        self.query_one("#depth", DepthPanel).display = self.cfg.show_depth
        self._set_chart_bar_ns(self._tf.bar_ns)
        self._set_chart_titles()
        self.query_one("#hist", HighLowGauges).window_labels = self.engine.cfg.window_labels

        for tid in ("new_lows", "session_highs"):
            t = self.query_one("#" + tid, DataTable)
            t.add_columns("Symbol", "Count", "Price", "%Chg")
            t.cursor_type = "row"   # row selection focuses the symbol on chart #1
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
            self._refresh_watchlist()
            self._refresh_quote_panel()
            self._refresh_depth_panel()
            self._draw_chart(   # chart pair #1: focus symbol
                "#price", "#volume", self._focus_candles,
                symbol=self.focus_symbol, strat_cfg=self._focus_strategy_cfg(),
            )
            self._draw_chart(   # chart pair #2: strategy symbol (SPY)
                "#price2", "#volume2", self._price_candles,
                symbol=self.cfg.strategy_symbol, strat_cfg=self.strategy.cfg,
            )
            self._update_header()
        except NoMatches:
            pass

    def _refresh_watchlist(self) -> None:
        """Rebuild the watchlist board rows from engine quotes; each snapshot
        also appends one price sample per symbol to its sparkline ring buffer."""
        watched = self._watchlist.items()
        live = {info.symbol for info in watched}
        for sym in [s for s in self._watch_prices if s not in live]:
            del self._watch_prices[sym]
        rows: list[WatchRow] = []
        for info in watched:
            quote = self.engine.quote(info.symbol)
            history = self._watch_prices.setdefault(info.symbol, deque(maxlen=SPARK_WINDOW))
            last: float | None = None
            pct: float | None = None
            if quote is not None:
                last, pct = quote
                history.append(last)
            rows.append(WatchRow(symbol=info.symbol, last=last, pct=pct,
                                 spark=sparkline(history)))
        self.query_default("#watchlist", WatchlistBoard).update_rows(rows, self)

    def _refresh_quote_panel(self) -> None:
        """Repaint the quote panel from cached engine/fundamentals state.

        Cheap by design (runs at 10 Hz inside sample_snapshot): engine reads
        are O(1) dict lookups and fundamentals come from the TTL cache — only
        a cache miss kicks the async fetch worker, and only for bare-equity
        focus on a live source with the equities feed enabled.
        """
        symbol = self.focus_symbol
        if ":" in symbol:
            asset = "CRYPTO"
        elif self._equity_source_resolved == "live":
            asset = "EQUITY"
        else:
            asset = "SIM"
        quote = self.engine.quote(symbol)
        rng = self.engine.session_range(symbol)
        show_fund = asset == "EQUITY"
        fund: Fundamentals | None = None
        if show_fund and self.cfg.enable_equities:
            entry = self._fundamentals_cache.get(symbol)
            if entry is not None and (
                self._fundamentals_now() - entry[0] < FUNDAMENTALS_TTL_S
            ):
                fund = entry[1]
            elif self._fundamentals_inflight != symbol:
                # Lazy one-shot per symbol per TTL; "—" placeholders meanwhile.
                self._fundamentals_inflight = symbol
                self._fetch_fundamentals(symbol)
        self.query_default("#quote", QuotePanel).state = QuoteState(
            symbol=symbol, asset=asset,
            last=quote[0] if quote is not None else None,
            pct=quote[1] if quote is not None else None,
            hi=rng[0] if rng is not None else None,
            lo=rng[1] if rng is not None else None,
            fundamentals=fund, show_fundamentals=show_fund,
        )

    @work(exclusive=True, group="fundamentals")
    async def _fetch_fundamentals(self, symbol: str) -> None:
        """Background fundamentals fetch for the quote panel.

        Failures are silent by contract (debug log + cached None) so a scrape
        hiccup can never disturb the UI loop; caching the failure also rate-
        limits retries to one per TTL. A cancellation (exclusive relaunch for
        a newer symbol) writes nothing — the finally only clears the marker.
        """
        try:
            data = await self._fundamentals_fetcher(symbol)
        except Exception:
            log.debug("fundamentals fetch failed for %s", symbol, exc_info=True)
            data = None
        finally:
            if self._fundamentals_inflight == symbol:
                self._fundamentals_inflight = None
        self._fundamentals_cache[symbol] = (self._fundamentals_now(), data)

    def _refresh_depth_panel(self) -> None:
        """Repaint the depth ladder from the TTL cache; a cache miss kicks the
        lazy one-shot fetch worker.

        Gated three ways so it stays cheap and correct: the panel must be
        VISIBLE (``:depth`` toggles it; hidden by default), the equities feed
        enabled, and the focus a BARE ticker on the LIVE source — synthetic/L1
        depth is an equities-only capability, so crypto/sim focus shows the
        ``—`` placeholder instead of firing a pointless fetch.
        """
        try:
            panel = self.query_default("#depth", DepthPanel)
        except NoMatches:
            return
        if not panel.display:
            return
        symbol = self.focus_symbol
        eligible = (
            ":" not in symbol
            and self._equity_source_resolved == "live"
            and self.cfg.enable_equities
        )
        if not eligible:
            panel.symbol = ""       # ineligible: badge falls back to "—"
            panel.view = None
            return
        panel.symbol = symbol       # keep the badge naming the focus symbol
        entry = self._depth_cache.get(symbol)
        if entry is not None and (self._depth_now() - entry[0] < DEPTH_TTL_S):
            panel.view = entry[1]
        elif self._depth_inflight != symbol:
            # Lazy one-shot per symbol per TTL; "—" placeholder meanwhile.
            self._depth_inflight = symbol
            panel.view = None
            self._fetch_depth(symbol)

    @work(exclusive=True, group="depth")
    async def _fetch_depth(self, symbol: str) -> None:
        """Background depth snapshot for the depth panel.

        Failures are silent by contract (debug log + cached None) so a Yahoo
        429 or an Alpaca auth error can never disturb the UI loop; caching the
        failure also rate-limits retries to one per TTL. An exclusive relaunch
        for a newer symbol cancels this cleanly — the finally only clears the
        marker, writing nothing for the abandoned symbol.
        """
        try:
            data = await self._depth_fetcher(symbol)
        except Exception:
            log.debug("depth fetch failed for %s", symbol, exc_info=True)
            data = None
        finally:
            if self._depth_inflight == symbol:
                self._depth_inflight = None
        self._depth_cache[symbol] = (self._depth_now(), data)

    def _set_chart_bar_ns(self, bar_ns: int) -> None:
        """Keep every chart's x-axis format in sync with the active timeframe."""
        try:
            self.query_default("#price", PriceChart).bar_ns = bar_ns
            self.query_default("#price2", PriceChart).bar_ns = bar_ns
            self.query_default("#volume", VolumeChart).bar_ns = bar_ns
            self.query_default("#volume2", VolumeChart).bar_ns = bar_ns
        except NoMatches:
            pass

    def _set_chart_titles(self) -> None:
        """Chart #1 titles after the focus symbol, chart #2 after the strategy
        symbol; both carry the active timeframe (refresh on either change)."""
        try:
            self.query_default("#price", PriceChart).title = (
                f"{self.focus_symbol} · {self._tf.name}"
            )
            self.query_default("#price2", PriceChart).title = (
                f"{self.cfg.strategy_symbol} · {self._tf.name}"
            )
        except NoMatches:
            pass

    def _focus_strategy_cfg(self) -> StrategyConfig:
        """EMA overlay periods for chart #1: the crypto strategy's when the
        focus symbol IS the crypto strategy symbol, the equity strategy's
        otherwise (an arbitrary focused symbol has no strategy of its own,
        so it borrows the equity strategy's fast/slow periods)."""
        if self.focus_symbol == self.cfg.crypto_strategy_symbol:
            return self.crypto_strategy.cfg
        return self.strategy.cfg

    def _draw_chart(
        self, price_id: str, vol_id: str, agg: CandleAggregator,
        *, symbol: str, strat_cfg: StrategyConfig,
    ) -> None:
        bars = agg.bars()
        last = bars[-1] if bars else None
        # Fingerprint of everything the pair renders from. Unchanged → skip
        # the full plotext clear+rebuild (this runs at 10 Hz for two pairs);
        # any new bar, tick on the last bar, chart-type/volume toggle, theme,
        # symbol, timeframe, or EMA-period change invalidates it.
        key: tuple[object, ...] = (
            len(bars),
            last.c if last is not None else None,
            last.vol if last is not None else None,
            self.cfg.chart_type,
            self.cfg.show_volume,
            self.theme,
            symbol,
            self._tf.name,
            strat_cfg.fast,
            strat_cfg.slow,
        )
        if not self._chart_memo.is_stale(price_id, key):
            return
        overlays: dict[str, list[float]] = {}
        mod = _ema_module()
        if mod is not None:
            closes = [b.c for b in bars]
            for period in (strat_cfg.fast, strat_cfg.slow):
                if 0 < period <= len(closes):  # skip until enough closes to warm up
                    ema = mod.calculate_ema(closes, period)
                    overlays[f"EMA{period}"] = [float(v) for v in ema if v is not None]
        try:
            price = self.query_default(price_id, PriceChart)
            volume = self.query_default(vol_id, VolumeChart)
        except NoMatches:
            return  # widgets not mounted: don't record, so a later draw retries
        price.set_series(
            [Candle(t=b.t, o=b.o, h=b.h, l=b.l, c=b.c) for b in bars],
            overlays or None,
        )
        volume.set_series([(b.t, b.vol) for b in bars], [b.c >= b.o for b in bars])
        self._chart_memo.record(price_id, key)

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
            header.market_status = self._market_status()
        except NoMatches:
            pass

    def _market_status(self) -> str:
        """NYSE chip state ("open"/"closed"/""), memoized: the 10 Hz snapshot
        timer refreshes the header, but the calendar math only reruns every 30s."""
        now = time.monotonic()
        last = self._market_status_ts
        if last is not None and now - last < _MARKET_STATUS_TTL_S:
            return self._market_status_cache
        self._market_status_ts = now
        self._market_status_cache = market_status()  # "" if stockodile is unavailable
        return self._market_status_cache

    def _push_info(self, text: str, color: str = "white") -> None:
        with suppress(NoMatches):
            self.query_default("#console", AlgoConsole).push_info(text, color)

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
        # SPY warms from real Yahoo bars when the live feed is active, from
        # synthesized sim bars otherwise; crypto always warms from real klines.
        self._warmup_spy()
        if self.cfg.enable_crypto:
            self._warmup_crypto()

    def _warmup_spy(self) -> None:
        """Warm the equity strategy: real Yahoo bars when the resolved source is
        live, instantly-synthesized flat bars otherwise (sim, or not yet resolved
        — on first boot _run_equity_feed re-warms once it settles on live)."""
        if self._equity_source_resolved == "live":
            self._warmup_equity()
            return
        self._push_events(self.strategy.warmup(self._synth_spy_bars()))
        self._push_info(f"watching [{self.cfg.strategy_symbol}]")

    @work(exclusive=True, group="equity_warmup")
    async def _warmup_equity(self) -> None:
        # exclusive (own group, so it can't cancel the crypto warmup): a newer
        # warmup after a rapid timeframe/symbol change cancels the in-flight
        # fetch, keeping stale bars away from the current strategy.
        symbol = self.cfg.strategy_symbol
        tf_name = self._tf.name
        try:
            bars = await warmup_equity_bars(symbol, tf_name)
        except Exception as exc:  # network/timeout/empty — warmup is best-effort.
            self._error_text = f"equity warmup failed: {exc}"
            self._push_info(f"equity warmup failed ({exc}); using synthetic bars",
                            "yellow")
            if not self.strategy.is_warm:  # boot path was already synth-warmed
                self._push_events(self.strategy.warmup(self._synth_spy_bars()))
                self._push_info(f"watching [{self.cfg.strategy_symbol}]")
            return
        # Post-await staleness guard (mirrors _warmup_focus): a settings save
        # while the fetch was in flight may have flipped the source back to sim
        # (the relaunched feed worker does NOT cancel this group) or moved the
        # symbol/timeframe on. Seeding real Yahoo bars onto a strategy/chart now
        # fed by ~$100 sim ticks would create a price discontinuity and could
        # journal spurious paper trades — discard the fetch instead.
        if (
            self._equity_source_resolved != "live"
            or self.cfg.strategy_symbol != symbol
            or self._tf.name != tf_name
        ):
            return  # stale fetch: source/symbol/timeframe moved on mid-flight
        # On first boot the mount-time warmup already synth-warmed this strategy
        # (live resolution happens later, inside the feed worker): rebuild it so
        # the real bars seed a clean EMA instead of appending to a synthetic one.
        # Live ticks during the fetch may have opened a position (its OPEN row is
        # already in the trade CSV) — transplant it so a later cross can still
        # close it; only the EMAs/_prev_sign reseed from the real bars.
        if self.strategy.is_warm:
            position = self.strategy.position
            self.strategy = Strategy(StrategyConfig(symbol=symbol))
            self.strategy.position = position
        events = self.strategy.warmup(bars)
        # Seed the SPY candle chart from the same bars (the sim path draws from
        # live sim ticks instead). Fresh aggregator: drops any synthetic-era
        # candles; per bar, close→high→low→close fills o/h/l/c in its bucket.
        agg = CandleAggregator(self._candle_interval_ns)
        for b in bars:
            agg.add(b.ts_ns, b.close, 0.0)
            if b.high is not None:
                agg.add(b.ts_ns, b.high, 0.0)
            if b.low is not None:
                agg.add(b.ts_ns, b.low, 0.0)
            agg.add(b.ts_ns, b.close, 0.0)
        self._price_candles = agg
        self._push_events([e for e in events if e.kind is EventKind.INFO])
        self._push_info(f"watching [{self.cfg.strategy_symbol}]")

    @work(exclusive=True, group="warmup")
    async def _warmup_crypto(self) -> None:
        # exclusive: a newer warmup (e.g. after a rapid symbol/timeframe change) cancels
        # any in-flight fetch, so a stale symbol's klines can't seed the current strategy.
        raw = self.cfg.crypto_strategy_symbol.split(":", 1)[-1]
        try:
            bars = await warmup_klines(raw, interval=self._tf.name)
        except Exception as exc:  # network/REST hiccup — warmup is best-effort.
            self._error_text = f"crypto warmup failed: {exc}"
            return
        if not bars:
            return
        events = self.crypto_strategy.warmup(bars)
        info = [e for e in events if e.kind is EventKind.INFO]
        self._push_events(info)

    def watch_focus_symbol(self, _old: str, new: str) -> None:
        """Chart #1 follows the focus symbol: swap in a fresh aggregator (so the
        previous symbol's candles never blend in), retitle, then warm up history
        in the background (best-effort)."""
        if not new:
            return
        self._focus_candles = CandleAggregator(self._tf.bar_ns)
        self._set_chart_titles()
        self._warmup_focus()

    @work(exclusive=True, group="focus_warmup")
    async def _warmup_focus(self) -> None:
        """Seed chart #1 with recent history for the focused symbol.

        Only ``binance-spot:RAW`` canonicals have a kline warmup source; other
        crypto venues (coinbase) skip silently. Bare equity tickers warm from
        Yahoo bars only when the resolved equity source is live — sim symbols
        have no history source beyond synth. Skipped symbols start empty and
        fill from live ticks. Exclusive group: a rapid focus change cancels the
        in-flight fetch; the post-await guard also drops a seed that a focus or
        timeframe change made stale mid-fetch.
        """
        symbol = self.focus_symbol
        tf_name = self._tf.name
        try:
            if ":" in symbol:
                venue, raw = symbol.split(":", 1)
                if venue != "binance-spot":
                    return  # no kline source for this venue — not an error
                bars = await warmup_klines(raw, tf_name)
            elif self._equity_source_resolved == "live":
                bars = await warmup_equity_bars(symbol, tf_name)
            else:
                return
        except Exception as exc:  # network/REST hiccup — warmup is best-effort.
            self._error_text = f"focus warmup failed: {exc}"
            self._push_info(
                f"focus warmup failed ({exc}); chart fills from live ticks", "yellow"
            )
            return
        if not bars or self.focus_symbol != symbol or self._tf.name != tf_name:
            return  # stale fetch: focus or timeframe moved on mid-flight
        # Per bar, close→high→low→close fills o/h/l/c in its bucket (same
        # seeding pattern as the SPY chart in _warmup_equity).
        agg = CandleAggregator(self._tf.bar_ns)
        for b in bars:
            agg.add(b.ts_ns, b.close, 0.0)
            if b.high is not None:
                agg.add(b.ts_ns, b.high, 0.0)
            if b.low is not None:
                agg.add(b.ts_ns, b.low, 0.0)
            agg.add(b.ts_ns, b.close, 0.0)
        self._focus_candles = agg

    def _feed_status(self, text: str, color: str = "white") -> None:
        """Surface transport connect/disconnect noise as console INFO lines."""
        self._push_info(text, color)

    @work(exclusive=True, group="drain")
    async def run_drain(self) -> None:
        q = self._sink.q
        drained = 0
        while True:
            r = await q.get()  # returns WITHOUT yielding while the queue is backed up
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
            # Under sustained load q.get() never suspends, which starves the 10 Hz
            # UI timers: hand the loop back every 200 records.
            drained += 1
            if drained % 200 == 0:
                await asyncio.sleep(0)

    def _on_strategy(self, r: Trade) -> None:
        strat = (
            self.crypto_strategy
            if r.symbol == self.cfg.crypto_strategy_symbol
            else self.strategy
        )
        sevs = strat.on_price(r.symbol, r.price, r.local_ts)
        if not sevs:
            return

        from entropy.bot.ledger import record_trade_close, record_trade_open
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
        # Chart #1 keys off the focus symbol; chart #2 stays hardwired to the
        # strategy symbol. Independent ifs: focusing the strategy symbol itself
        # feeds both charts.
        if r.symbol == self.focus_symbol:
            self._focus_candles.add(r.local_ts, r.price, r.amount)
        if r.symbol == self.cfg.strategy_symbol:
            self._price_candles.add(r.local_ts, r.price, r.amount)

    @work(exclusive=True, group="equity_feed")
    async def _run_equity_feed(self) -> None:
        # exclusive: a settings-driven relaunch cancels the previous worker first,
        # so the sim and live feeds can never run side by side.
        try:
            source = resolve_equity_source(self.cfg.equity_source)
        except Exception as exc:
            self._feed_status(f"equities: source resolution failed ({exc}); using sim", "red")
            source = "sim"
        self._equity_source_resolved = source
        if source == "live":
            try:
                task, plan = await start_equity_feed(self._sink, LIVE_UNIVERSE)
            except Exception as exc:
                self._feed_status(
                    f"equities: live feed failed ({exc}); falling back to sim", "red"
                )
                self._error_text = f"equity feed: {exc}"
                self._equity_source_resolved = "sim"
            else:
                self._feed_status(f"equities: source=live ({plan.provider_name})")
                if plan.provider_name == "google_finance":
                    # Keyless fallback: Google polls ~10s quotes and synthesizes
                    # ticks from them — flag it so the gauges aren't over-read.
                    self._feed_status(
                        "equities: google_finance serves ~10s synthetic quotes; "
                        "breadth/momentum are approximate", "yellow")
                if plan.trimmed_symbols:
                    self._feed_status(
                        f"equities: {plan.provider_name} symbol cap dropped "
                        f"{len(plan.trimmed_symbols)}: {', '.join(plan.trimmed_symbols)}",
                        "yellow",
                    )
                # Real tape from here on: re-warm SPY from real Yahoo bars (the
                # mount-time warmup ran before this worker resolved the source).
                self._warmup_equity()
                try:
                    await task
                except Exception as exc:
                    # Mid-run death: report, don't splice sim prices onto real ones.
                    self._feed_status(f"equities: live feed stopped ({exc})", "red")
                    self._error_text = f"equity feed: {exc}"
                finally:
                    # Worker cancelled (settings change / quit) or exited: the
                    # collect task must die with it, not keep feeding the sink.
                    task.cancel()
                return
        self._feed_status("equities: source=sim")
        await self._equity.run()

    @work(exclusive=True, group="crypto_feed")
    async def _run_crypto_feed(self) -> None:
        # exclusive + own group: the Settings crypto toggle cancels/relaunches this
        # worker without touching the equity feed's group.
        # Reconnect noise lives in the console layer (keeps the engine pure).
        self._feed_status("connecting…")
        try:
            header = self.query_default("#header", HeaderBar)
        except Exception:
            header = None
        try:
            task = await start_feed(self._sink)
        except Exception as exc:
            if header is not None:
                header.sources = "coinbase ○  binance ○"   # never connected
            self._feed_status(f"disconnect: {exc}", "red")
            self._error_text = f"crypto feed: {exc}"
            return
        try:
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
        finally:
            # Worker cancelled (settings toggle / quit) or exited: the collect task
            # must die with it, not keep feeding the sink (mirrors the equity path).
            task.cancel()

    def action_help(self) -> None:
        self.push_screen(HelpScreen(id="help"))

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen(id="settings"))

    def action_errors(self) -> None:
        self.push_screen(ErrorScreen(self._error_text, id="errors"))

    def action_search(self) -> None:
        self.push_screen(SearchScreen(id="search"))

    def action_command_bar(self) -> None:
        """`:`: reveal + focus the Bloomberg-style command line."""
        self.query_default("#cmdbar", CommandBar).show()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the command bar executes; valid commands hide the bar,
        errors keep it open for correction. Other Inputs' submits (settings
        fields, modal search) are filtered out by id."""
        if event.input.id != "cmdbar":
            return
        if self._execute_command(event.value):
            self.query_default("#cmdbar", CommandBar).hide()

    def _execute_command(self, text: str) -> bool:
        """Run one command-bar command; True = handled (hide the bar),
        False = parse/validation error (bar stays open). Feedback lands on
        the console in the existing `equities: ...` line style."""
        result = parse_command(text)
        if isinstance(result, CommandError):
            self._push_info(f"cmd: {result.message}", "red")
            return False
        verb, arg = result.verb, result.arg
        if verb == "chart":
            self.focus_symbol = arg
            self._push_info(f"cmd: chart → [{arg}]")
        elif verb in ("watch", "unwatch"):
            info = self._resolve_symbol(arg)
            present = info.symbol in self._watchlist
            if (verb == "watch") == present:  # already in the requested state
                state = "watched" if present else "not watched"
                self._push_info(f"cmd: [{info.symbol}] already {state}")
            else:
                self.toggle_watch(info)   # pushes its own "watchlist ±" line
        elif verb == "tf":
            self._apply_settings_patch(timeframe=arg)
            self._push_info(f"cmd: timeframe → {arg}")
        elif verb == "theme":
            if arg not in self.available_themes:
                self._push_info(f"cmd: unknown theme {arg!r}", "red")
                return False
            self._apply_settings_patch(theme=arg)
            self._push_info(f"cmd: theme → {arg}")
        elif verb == "source":
            self._apply_settings_patch(equity_source=arg)
            self._push_info(f"cmd: equity source → {arg}")
        elif verb == "depth":
            panel = self.query_default("#depth", DepthPanel)
            if arg:                       # `depth SYM`: focus it + ensure visible
                self.focus_symbol = arg
                panel.display = True
                self._push_info(f"cmd: depth → [{arg}]")
            else:                         # `depth`: toggle the ladder
                panel.display = not panel.display
                self._push_info(f"cmd: depth {'on' if panel.display else 'off'}")
            # Persist the toggle so a later settings rebuild reinstates it.
            self.cfg = msgspec.structs.replace(self.cfg, show_depth=bool(panel.display))
        else:  # help — parse_command admits no other verb
            self.push_screen(HelpScreen(id="help"))
        return True

    def _apply_settings_patch(self, **overrides: Any) -> None:
        """Re-drive _apply_settings (the settings modal's single hot-apply
        path) with the current config plus ``overrides`` — the command bar
        changes one field without duplicating any rebuild/relaunch logic."""
        cfg = self.cfg
        kwargs: dict[str, Any] = {
            "theme": cfg.theme, "chart_type": cfg.chart_type,
            "show_volume": cfg.show_volume, "timeframe": cfg.timeframe,
            "enable_equities": cfg.enable_equities, "enable_crypto": cfg.enable_crypto,
            "equity_source": cfg.equity_source, "equity_tps": cfg.equity_tps,
            "strategy_symbol": cfg.strategy_symbol,
            "crypto_strategy_symbol": cfg.crypto_strategy_symbol,
            "spike_pct": cfg.engine.spike_pct, "snapdrop_pct": cfg.engine.snapdrop_pct,
        }
        kwargs.update(overrides)
        self._apply_settings(**kwargs)

    def action_watch_toggle(self) -> None:
        """`w`: toggle the focused symbol in the persistent watchlist."""
        self.toggle_watch(self._resolve_symbol(self.focus_symbol))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Any board/watchlist row (keyed by symbol) focuses that symbol."""
        symbol = event.row_key.value
        if symbol:
            self.focus_symbol = symbol

    def _resolve_symbol(self, symbol: str) -> SymbolInfo:
        """SymbolInfo via exact universe match, else a bare fallback whose
        asset class comes from the ':' venue-prefix heuristic."""
        for info in self._universe.search(symbol, limit=5):
            if info.symbol == symbol:
                return info
        has_venue = ":" in symbol
        return SymbolInfo(
            symbol=symbol,
            name=symbol,
            asset_class="crypto" if has_venue else "equity",
            venue=symbol.split(":", 1)[0] if has_venue else "us",
        )

    def toggle_watch(self, info: SymbolInfo) -> bool:
        """Toggle ``info`` in the watchlist (persisted); returns presence after.

        A failed disk write leaves memory unchanged (Watchlist guarantees it);
        surface it on the console and in the error screen instead of crashing.
        """
        try:
            present = self._watchlist.toggle(info)
        except OSError as exc:
            self._error_text = f"watchlist save failed: {exc}"
            self._push_info(f"watchlist save failed ({exc})", "red")
            return info.symbol in self._watchlist
        self._push_info(f"watchlist {'+' if present else '-'} [{info.symbol}]")
        return present

    def _apply_settings(
        self, *, theme: str, chart_type: str, show_volume: bool, timeframe: str,
        enable_equities: bool, enable_crypto: bool, equity_source: str, equity_tps: int,
        strategy_symbol: str, crypto_strategy_symbol: str,
        spike_pct: float, snapdrop_pct: float,
    ) -> None:
        tf_changed = timeframe != self.cfg.timeframe
        equity_source_changed = equity_source != self.cfg.equity_source
        equities_toggled = enable_equities != self.cfg.enable_equities
        crypto_toggled = enable_crypto != self.cfg.enable_crypto
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
            equity_source=equity_source, equity_tps=equity_tps, strategy_symbol=strategy_symbol,
            crypto_strategy_symbol=crypto_strategy_symbol, engine=new_engine_cfg,
        )
        self.theme = theme
        self.query_default("#price", PriceChart).chart_type = chart_type
        self.query_default("#price2", PriceChart).chart_type = chart_type
        self.query_default("#volume", VolumeChart).display = show_volume
        self.query_default("#volume2", VolumeChart).display = show_volume
        if self._equity is not None:
            self._equity.tps = equity_tps

        # Rebuild strategies FIRST (no warmup yet) so the warmup below runs exactly once
        # against fresh objects. A timeframe change also rebuilds them: re-warming a live
        # strategy in place would append synthetic bars onto its existing EMA and flip
        # _prev_sign while leaving an open position stranded, so a fresh start is correct.
        strat_symbol_changed = self.strategy.cfg.symbol != strategy_symbol
        crypto_symbol_changed = self.crypto_strategy.cfg.symbol != crypto_strategy_symbol
        if strat_symbol_changed or tf_changed:
            self.strategy = Strategy(StrategyConfig(symbol=strategy_symbol))
        if crypto_symbol_changed or tf_changed:
            self.crypto_strategy = Strategy(
                StrategyConfig(symbol=crypto_strategy_symbol, fee_bps=1.0)
            )

        if tf_changed:
            self._tf = spec
            self.engine = Engine(new_engine_cfg)
            self._candle_interval_ns = spec.bar_ns
            self._warmup_bars = spec.warmup_bars
            self._warmup_dt_ns = spec.bar_ns
            self._price_candles = CandleAggregator(spec.bar_ns)
            self._focus_candles = CandleAggregator(spec.bar_ns)
            self._set_chart_bar_ns(spec.bar_ns)
            self.query_default("#hist", HighLowGauges).window_labels = spec.window_labels
            self._warmup_strategies()  # warms equity + (if enabled) crypto once, with new symbols
        else:
            self.engine.cfg = new_engine_cfg
            if strat_symbol_changed:
                self._warmup_spy()   # real bars when live, synth otherwise
            if crypto_symbol_changed:
                self._warmup_crypto()
        # Chart #2's title tracks strategy_symbol; both titles track the timeframe.
        self._set_chart_titles()

        # Feed enable/disable transitions (the mount-time launch only ever ran once;
        # these switches must keep working for the app's whole lifetime).
        if equities_toggled:
            if enable_equities:
                self._push_info("equities: feed enabled")
                self._run_equity_feed()
            else:
                self.workers.cancel_group(self, "equity_feed")
                self._push_info("equities: feed disabled")
        elif equity_source_changed and self.cfg.enable_equities:
            # Relaunch resolves the new source; exclusive worker cancels the old feed.
            self._run_equity_feed()

        if crypto_toggled:
            if enable_crypto:
                self._push_info("crypto: feed enabled")
                if not self.crypto_strategy.is_warm:  # first enable: mirror on_mount
                    self._warmup_crypto()
                self._run_crypto_feed()
            else:
                # Cancelling the worker triggers its finally: the collect task dies too.
                self.workers.cancel_group(self, "crypto_feed")
                with suppress(NoMatches):
                    self.query_default("#header", HeaderBar).sources = "equities only"
                self._push_info("crypto: feed disabled")
