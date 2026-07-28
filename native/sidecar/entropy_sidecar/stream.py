"""Headless engine + feeds + bot behind one ``build()`` -> SnapshotMessage.

This is the GUI's whole backend. Everything the native app can show has to come
through here, so the module owns four things that used to be hardcoded or
missing entirely: the persisted AppConfig/BotConfig (hot-applied AND saved), the
two market feeds as independently restartable tasks, the persistent watchlist,
and the trading bot — driven from the sidecar's own drain rather than its own.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

import msgspec
from crocodile.core.schema.records import Trade

from entropy import settings as settings_store
from entropy.app import AppConfig
from entropy.bot.config import BotConfig
from entropy.bot.config import validate as validate_bot
from entropy.bot.runner import BotRunner
from entropy.config import EngineConfig
from entropy.data.universe import SymbolInfo, UniverseService, make_symbol_info
from entropy.data.watchlist import Watchlist
from entropy.engine.candles import CandleAggregator
from entropy.engine.engine import Engine
from entropy.engine.timeframe import (
    CHART_INTERVALS,
    TIMEFRAMES,
    chart_warmup_interval,
    get_timeframe,
    resolve_chart_interval,
)
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed
from entropy.feeds.equities.source import market_status, resolve_equity_source
from entropy.feeds.equities.universe import LIVE_UNIVERSE
from entropy.feeds.warmup import warmup_equity_bars, warmup_klines
from entropy.ui.widgets.depth_panel import fetch_depth
from entropy.ui.widgets.quote_panel import FUNDAMENTALS_TTL_S, fetch_fundamentals_google
from entropy.ui.widgets.watchlist_board import SPARK_WINDOW
from entropy_sidecar.contract import (
    CHART_TYPES,
    EQUITY_SOURCES,
    THEMES,
    BotPosition,
    BotStrategy,
    BotView,
    DepthLevels,
    FeedStatus,
    FocusView,
    Fundamentals,
    SettingsView,
    SnapshotMessage,
    WatchRow,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

DepthFetcher = Callable[[str], Awaitable[Any]]
FundamentalsFetcher = Callable[[str], Awaitable[Any]]
BarFetcher = Callable[[str, str], Awaitable[list[Any]]]

_MARKET_STATUS_TTL_S = 30.0     # calendar math is memoized; the stream runs at 10 Hz
_BOT_EQUITY_PERIOD_S = 1.0      # how often the bot's equity curve is journalled


async def start_equity_feed(sink: Any, symbols: Sequence[str]) -> tuple[asyncio.Task[Any], Any]:
    """Lazy indirection over the live module: the sim path must never import
    crocodile, and tests monkeypatch this symbol to stub the live feed."""
    from entropy.feeds.equities import live
    return await live.start_equity_feed(sink, symbols)


async def start_crypto_feed(sink: Any) -> asyncio.Task[Any]:
    """Same indirection for crypto (patchable, and keeps the WS deps lazy)."""
    from entropy.feeds.crypto import start_feed
    return await start_feed(sink)


def validate_app(cfg: AppConfig) -> list[str]:
    """Human-readable problems with ``cfg``; empty means it is safe to apply.

    Mirrors entropy.bot.config.validate for the app half. Without it a bad
    timeframe would surface as a KeyError from inside the hot-apply path, i.e.
    a 500 after the settings had already half-changed.
    """
    problems: list[str] = []
    if cfg.timeframe not in TIMEFRAMES:
        problems.append(f"unknown timeframe {cfg.timeframe!r}")
    if cfg.chart_interval and cfg.chart_interval not in CHART_INTERVALS:
        problems.append(f"unknown chart interval {cfg.chart_interval!r}")
    if cfg.chart_bars < 2:
        problems.append("chart must keep at least 2 bars")
    if cfg.equity_source not in EQUITY_SOURCES:
        problems.append(f"unknown equity source {cfg.equity_source!r}")
    if cfg.theme not in THEMES:
        problems.append(f"unknown theme {cfg.theme!r}")
    if cfg.chart_type not in CHART_TYPES:
        problems.append(f"unknown chart type {cfg.chart_type!r}")
    if cfg.equity_tps <= 0:
        problems.append("equity sim ticks/sec must be positive")
    if cfg.depth_bins < 1 or cfg.depth_top_n < 1:
        problems.append("depth bins / top N must be >= 1")
    return problems


def _watchlist_path(cfg: AppConfig) -> Path:
    if cfg.watchlist_path:
        return Path(cfg.watchlist_path)
    return Path.home() / ".entropy" / "watchlist.json"


def _default_bot_run_dir() -> str:
    """Ledger location, anchored at ~/.entropy rather than the CWD.

    The packaged sidecar is spawned by the Tauri shell with no guarantee about
    its working directory (it can be "/"), so a relative "runs/..." would fail
    to create on the very first bot start.
    """
    return str(Path.home() / ".entropy" / "runs" / "native")


class SnapshotSource:
    """Owns the engine, both feeds, the watchlist, the settings and the bot.

    ``build()`` assembles one SnapshotMessage; ``start_feeds()`` spawns the
    background tasks (the app's lifespan calls it). Tests drive ``engine.on_trade``
    directly without starting feeds, and inject the depth/fundamentals/warmup
    fetchers so nothing touches the network.
    """

    def __init__(
        self,
        *,
        cfg: AppConfig | None = None,
        bot_cfg: BotConfig | None = None,
        depth_fetcher: DepthFetcher = fetch_depth,
        fundamentals_fetcher: FundamentalsFetcher = fetch_fundamentals_google,
        klines_fetcher: BarFetcher | None = None,
        equity_bars_fetcher: BarFetcher | None = None,
        market_status_fn: Callable[[], str] = market_status,
        bot_run_dir: str | None = None,
    ) -> None:
        stored = settings_store.load()
        self.cfg = cfg if cfg is not None else stored.app
        self.bot_cfg = bot_cfg if bot_cfg is not None else stored.bot
        self.engine = Engine(EngineConfig.from_timeframe(get_timeframe(self.cfg.timeframe)))
        self._focus = self.cfg.strategy_symbol
        self._depth_fetcher = depth_fetcher
        self._fundamentals_fetcher = fundamentals_fetcher
        self._klines_fetcher: BarFetcher = klines_fetcher or (
            lambda sym, interval: warmup_klines(sym, interval=interval)
        )
        self._equity_bars_fetcher: BarFetcher = equity_bars_fetcher or (
            lambda sym, interval: warmup_equity_bars(sym, interval)
        )
        self._market_status_fn = market_status_fn
        self._sink = QueueSink()

        # Concrete "sim"/"live" the equity feed settled on. Seeded optimistically
        # from the config (never resolved here: "auto" resolution imports
        # crocodile, which construction must stay free of).
        self.source = self.cfg.equity_source if self.cfg.equity_source in ("sim", "live") else "sim"

        self._chart_interval, self._chart_bar_ns = resolve_chart_interval(
            self.cfg.chart_interval, self.cfg.timeframe
        )
        self._focus_candles = CandleAggregator(self._chart_bar_ns, maxlen=self.cfg.chart_bars)

        # One task per concern, keyed so a settings change can restart exactly
        # one feed. A crypto toggle must never disturb the equity tape.
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._feeds_started = False
        self._feed_equities = "off"
        self._feed_crypto = "off"
        self._feed_detail = ""

        self._watchlist = Watchlist(_watchlist_path(self.cfg))
        self._universe = UniverseService()
        self._watch_prices: dict[str, deque[float]] = {}

        self._market_status_cache = ""
        self._market_status_ts: float | None = None
        self._fundamentals_cache: dict[str, tuple[float, Fundamentals | None]] = {}
        self._fundamentals_inflight: str | None = None

        self.bot: BotRunner | None = None
        self._bot_running = False
        self._bot_run_dir = bot_run_dir or _default_bot_run_dir()
        self._last_ts_ns = 0

    # --- focus ---------------------------------------------------------------

    def set_focus(self, symbol: str) -> None:
        new = symbol.upper() if ":" not in symbol else symbol
        if new == self._focus:
            return
        self._focus = new
        # Fresh aggregator so the previous symbol's candles never blend in.
        self._focus_candles = CandleAggregator(self._chart_bar_ns, maxlen=self.cfg.chart_bars)
        self._schedule_chart_warmup()

    @property
    def focus(self) -> str:
        return self._focus

    # --- settings ------------------------------------------------------------

    def apply_app(self, cfg: AppConfig) -> tuple[list[str], str]:
        """Validate, hot-apply and persist an AppConfig.

        Returns ``(problems, persist_error)``. A non-empty ``problems`` means
        NOTHING changed. A non-empty ``persist_error`` means the change is live
        but did not reach disk — reported rather than swallowed.
        """
        problems = validate_app(cfg)
        if problems:
            return problems, ""
        self._hot_apply_app(cfg)
        try:
            settings_store.save_app(cfg)
        except OSError as exc:
            return [], f"settings not saved to disk: {exc}"
        return [], ""

    def patch_app(self, **overrides: Any) -> tuple[list[str], str]:
        return self.apply_app(msgspec.structs.replace(self.cfg, **overrides))

    def apply_bot(self, cfg: BotConfig) -> tuple[list[str], str]:
        """Same contract as :meth:`apply_app` for the bot half."""
        problems = validate_bot(cfg)
        if problems:
            return problems, ""
        if self.bot is not None:
            problems = self.bot.apply_config(cfg)
            if problems:      # re-validated inside the runner; report, change nothing
                return problems, ""
        self.bot_cfg = cfg
        try:
            settings_store.save_bot(cfg)
        except OSError as exc:
            return [], f"settings not saved to disk: {exc}"
        return [], ""

    def _hot_apply_app(self, cfg: AppConfig) -> None:
        old, self.cfg = self.cfg, cfg
        if cfg.timeframe != old.timeframe:
            # Per-symbol history is rebuilt from scratch: the rolling windows
            # ARE the timeframe, so carrying old tapes across would mix cadences.
            self.engine = Engine(EngineConfig.from_timeframe(get_timeframe(cfg.timeframe)))
        if (cfg.chart_interval, cfg.timeframe, cfg.chart_bars) != (
            old.chart_interval, old.timeframe, old.chart_bars
        ):
            self._rebuild_chart()
        if cfg.watchlist_path != old.watchlist_path:
            self._watchlist = Watchlist(_watchlist_path(cfg))
            self._watch_prices.clear()
        if not self._feeds_started:
            return
        if (cfg.enable_equities, cfg.equity_source, cfg.seed, cfg.equity_tps) != (
            old.enable_equities, old.equity_source, old.seed, old.equity_tps
        ):
            self._restart_equities()
        if cfg.enable_crypto != old.enable_crypto:
            self._restart_crypto()

    def _rebuild_chart(self) -> None:
        self._chart_interval, self._chart_bar_ns = resolve_chart_interval(
            self.cfg.chart_interval, self.cfg.timeframe
        )
        self._focus_candles = CandleAggregator(self._chart_bar_ns, maxlen=self.cfg.chart_bars)
        self._schedule_chart_warmup()

    # --- watchlist -----------------------------------------------------------

    def watched(self) -> list[SymbolInfo]:
        return self._watchlist.items()

    def is_watched(self, symbol: str) -> bool:
        return symbol in self._watchlist

    def resolve_symbol(self, symbol: str) -> SymbolInfo:
        """SymbolInfo via exact universe match, else derived from the symbol.

        Both branches go through the shared constructor, so a name the universe
        has never heard of still arrives with a ticker, an exchange badge and a
        readable pair name instead of a raw ``venue:PAIR`` in every column.
        """
        for info in self._universe.search(symbol, limit=5):
            if info.symbol == symbol:
                return info
        return make_symbol_info(symbol)

    def add_watch(self, symbol: str) -> tuple[bool, str]:
        info = self.resolve_symbol(symbol)
        try:
            added = self._watchlist.add(info)
        except OSError as exc:
            return False, f"watchlist save failed: {exc}"
        if not added:
            return False, f"{info.symbol} is already watched"
        return True, f"watching {info.symbol}"

    def remove_watch(self, symbol: str) -> tuple[bool, str]:
        try:
            removed = self._watchlist.remove(symbol)
        except OSError as exc:
            return False, f"watchlist save failed: {exc}"
        if not removed:
            return False, f"{symbol} is not watched"
        self._watch_prices.pop(symbol, None)
        return True, f"unwatched {symbol}"

    def search_symbols(self, query: str, limit: int = 20) -> list[SymbolInfo]:
        return self._universe.search(query, limit=limit)

    # --- feeds ---------------------------------------------------------------

    async def start_feeds(self) -> None:
        """Spawn the drain plus whichever feeds the config enables (idempotent)."""
        self._feeds_started = True
        if "drain" not in self._tasks:
            self._tasks["drain"] = asyncio.create_task(self._drain())
        self._restart_equities()
        self._restart_crypto()

    async def stop_feeds(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._feeds_started = False
        self._feed_equities = "off"
        self._feed_crypto = "off"

    def _spawn(self, name: str, coro: Any) -> None:
        old = self._tasks.pop(name, None)
        if old is not None:
            old.cancel()
        self._tasks[name] = asyncio.create_task(coro)

    def _cancel(self, name: str) -> None:
        task = self._tasks.pop(name, None)
        if task is not None:
            task.cancel()

    def _restart_equities(self) -> None:
        if not self.cfg.enable_equities:
            self._cancel("equities")
            self._feed_equities = "off"
            return
        self._spawn("equities", self._run_equities())

    def _restart_crypto(self) -> None:
        if not self.cfg.enable_crypto:
            self._cancel("crypto")
            self._feed_crypto = "off"
            return
        self._spawn("crypto", self._run_crypto())

    async def _run_equities(self) -> None:
        try:
            source = resolve_equity_source(self.cfg.equity_source)
        except Exception as exc:
            self._feed_detail = f"equity source resolution failed ({exc}); using sim"
            source = "sim"
        self.source = source
        if source == "live":
            try:
                task, plan = await start_equity_feed(self._sink, LIVE_UNIVERSE)
            except Exception as exc:
                # Honest downgrade: say WHY the live tape is unavailable instead
                # of quietly serving sim prices labelled "live".
                self._feed_equities = "error"
                self._feed_detail = f"live equity feed failed ({exc}); falling back to sim"
                self.source = "sim"
            else:
                self._feed_equities = "live"
                self._feed_detail = f"equities: {plan.provider_name}"
                self._schedule_chart_warmup()
                try:
                    await task
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._feed_equities = "error"
                    self._feed_detail = f"live equity feed stopped ({exc})"
                finally:
                    # Cancelled here (settings change / shutdown) or exited: the
                    # collect task must die with us, not keep feeding the sink.
                    task.cancel()
                return
        self._feed_equities = "sim"
        feed = EquitySimFeed(self._sink, seed=self.cfg.seed, ticks_per_sec=self.cfg.equity_tps)
        await feed.run()

    async def _run_crypto(self) -> None:
        self._feed_crypto = "connecting"
        try:
            task = await start_crypto_feed(self._sink)
        except Exception as exc:
            self._feed_crypto = "error"
            self._feed_detail = f"crypto feed failed ({exc})"
            return
        try:
            self._feed_crypto = "live"
            await task
            self._feed_crypto = "off"
        except asyncio.CancelledError:
            self._feed_crypto = "off"
            raise
        except Exception as exc:
            self._feed_crypto = "error"
            self._feed_detail = f"crypto feed stopped ({exc})"
        finally:
            task.cancel()

    async def _drain(self) -> None:
        q = self._sink.q
        drained = 0
        while True:
            r = await q.get()
            if isinstance(r, Trade):
                self.engine.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)
                self._last_ts_ns = r.local_ts
                if r.symbol == self._focus:
                    self._focus_candles.add(r.local_ts, r.price, r.amount)
                bot = self.bot
                if bot is not None and self._bot_running:
                    try:
                        bot.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)
                    except Exception as exc:
                        # A strategy/risk bug must degrade the bot, not the tape:
                        # record it and keep draining for everything else.
                        self._feed_detail = f"bot error on {r.symbol}: {exc}"
                        log.debug("bot on_trade failed", exc_info=True)
            # Under sustained load q.get() never suspends; hand back periodically
            # so the WS send loop isn't starved.
            drained += 1
            if drained % 200 == 0:
                await asyncio.sleep(0)

    # --- chart warmup --------------------------------------------------------

    def _schedule_chart_warmup(self) -> None:
        """Best-effort history seed for the focus chart, if a loop is running.

        Callers are synchronous (set_focus, settings hot-apply) and some run
        outside any loop in tests, so a missing loop simply means "no warmup".
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._spawn("chart_warmup", self._warm_chart(self._focus, self._chart_bar_ns))

    async def _warm_chart(self, symbol: str, bar_ns: int) -> None:
        crypto = ":" in symbol
        usable = chart_warmup_interval(self._chart_interval, crypto=crypto)
        if usable is None:
            return          # no provider serves this width: fill from live ticks
        try:
            if crypto:
                venue, raw = symbol.split(":", 1)
                if venue != "binance-spot":
                    return  # only Binance serves klines here — not an error
                bars = await self._klines_fetcher(raw, usable)
            elif self.source == "live":
                bars = await self._equity_bars_fetcher(symbol, usable)
            else:
                return      # sim prices have no history source
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("chart warmup failed for %s", symbol, exc_info=True)
            return
        if not bars or symbol != self._focus or bar_ns != self._chart_bar_ns:
            return          # stale: focus or chart width moved on mid-fetch
        agg = CandleAggregator(bar_ns, maxlen=self.cfg.chart_bars)
        for b in bars:
            # close -> high -> low -> close fills o/h/l/c inside one bucket.
            agg.add(b.ts_ns, b.close, 0.0)
            if b.high is not None:
                agg.add(b.ts_ns, b.high, 0.0)
            if b.low is not None:
                agg.add(b.ts_ns, b.low, 0.0)
            agg.add(b.ts_ns, b.close, 0.0)
        self._focus_candles = agg

    # --- bot -----------------------------------------------------------------

    async def start_bot(self) -> tuple[bool, str, list[str]]:
        problems = validate_bot(self.bot_cfg)
        if problems:
            return False, "bot config is not usable", problems
        if self.bot is None:
            try:
                self.bot = BotRunner(self.bot_cfg, run_dir=self._bot_run_dir)
            except OSError as exc:
                # The ledger opens its files eagerly; an unwritable run dir or
                # trade CSV must surface as "the bot did NOT start", not a 500.
                return False, "bot could not open its ledger", [str(exc)]
        if self._bot_running:
            return True, "bot already running", []
        # DO NOT call BotRunner.run(): it opens its OWN sim + crypto feeds, which
        # would run a second tape alongside the sidecar's and double every tick.
        # The bot is driven from _drain instead; the only piece of run() we need
        # is its equity-curve cadence, replicated in _bot_equity_loop.
        await self.bot.warmup()
        self.bot.set_paused(False)
        self._bot_running = True
        self._spawn("bot_equity", self._bot_equity_loop())
        return True, "bot started", []

    def stop_bot(self) -> tuple[bool, str, list[str]]:
        if self.bot is None or not self._bot_running:
            return False, "bot is not running", []
        self._bot_running = False
        self._cancel("bot_equity")
        return True, "bot stopped", []

    def set_bot_paused(self, paused: bool) -> tuple[bool, str, list[str]]:
        if self.bot is None:
            return False, "bot has not been started", []
        if self.bot.paused == paused:
            return False, f"bot is already {'paused' if paused else 'running'}", []
        self.bot.set_paused(paused)
        return True, "bot paused" if paused else "bot resumed", []

    def halt_bot(self) -> tuple[bool, str, list[str]]:
        if self.bot is None:
            return False, "bot has not been started", []
        self.bot.trip_circuit_breaker()
        return True, "circuit breaker tripped; positions closed", []

    async def _bot_equity_loop(self) -> None:
        while True:
            await asyncio.sleep(_BOT_EQUITY_PERIOD_S)
            bot = self.bot
            if bot is None or not self._bot_running:
                continue
            try:
                # Private, deliberately: the daily-loss kill switch only resets
                # per UTC day if something calls this, and run() (which normally
                # would) is the very thing we must not use here.
                bot._maybe_rollover_day()
                bot.ledger.record_equity(bot.portfolio.snapshot(self._last_ts_ns))
            except Exception:
                log.debug("bot equity record failed", exc_info=True)

    def _bot_view(self) -> BotView | None:
        bot = self.bot
        if bot is None:
            return None
        snap = bot.snapshot()
        pf = snap.portfolio
        return BotView(
            running=self._bot_running, paused=bot.paused, halted=snap.halted, warm=snap.warm,
            mode=snap.mode, timeframe=snap.timeframe, bar_s=snap.bar_s,
            risk_profile=snap.risk_profile.name,
            risk_description=snap.risk_profile.description,
            ticks=snap.ticks, cash=pf.cash, equity=pf.equity,
            realized_pnl=pf.realized_pnl, unrealized_pnl=pf.unrealized_pnl,
            daily_pnl=pf.daily_pnl, open_count=pf.open_count,
            positions=[
                BotPosition(
                    symbol=p.symbol, side=str(p.side), qty=p.qty, entry_px=p.entry_px,
                    mark_px=p.mark_px, unrealized_pnl=p.unrealized_pnl,
                    stop_px=p.stop_px, tp_px=p.tp_px,
                )
                for p in pf.positions
            ],
            strategies=[
                BotStrategy(name=s.name, warm=s.warm, regimes=dict(s.regimes),
                            directions=dict(s.directions))
                for s in snap.strategies
            ],
            last_signals=list(snap.last_signals), last_rejects=list(snap.last_rejects),
        )

    # --- snapshot ------------------------------------------------------------

    async def build(self) -> SnapshotMessage:
        snap = self.engine.snapshot()
        leaders = lambda rows: [(r.symbol, r.count, r.price, r.pct_chg) for r in rows]  # noqa: E731
        ticker = [(g.window, [(s, c) for s, c in g.entries]) for g in snap.ticker]
        focus = await self._build_focus(self._focus)
        cfg = self.cfg
        return SnapshotMessage(
            schema_version=SCHEMA_VERSION, ts_ns=snap.ts_ns,
            buy_pct=snap.breadth.buy_pct, sell_pct=snap.breadth.sell_pct,
            raw_hz=snap.breadth.raw_hz, accel=snap.breadth.accel,
            new_highs=leaders(snap.new_highs), new_lows=leaders(snap.new_lows),
            ticker=ticker, focus=focus, watchlist=self._watch_rows(),
            market_status=self._market_status(), source=self.source,
            settings=SettingsView(
                timeframe=cfg.timeframe, chart_interval=cfg.chart_interval,
                chart_type=cfg.chart_type, show_volume=cfg.show_volume,
                show_depth=cfg.show_depth, equity_source=cfg.equity_source,
                enable_equities=cfg.enable_equities, enable_crypto=cfg.enable_crypto,
                theme=cfg.theme,
            ),
            feeds=FeedStatus(
                equities=self._feed_equities, crypto=self._feed_crypto,
                detail=self._feed_detail,
            ),
            bot=self._bot_view(),
        )

    def _watch_rows(self) -> list[WatchRow]:
        """Watchlist rows from engine quotes; each build appends one price
        sample per symbol to its sparkline ring."""
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
            rows.append((info.symbol, info.ticker or info.symbol, info.name,
                         info.exchange, last, pct, list(history)))
        return rows

    def _market_status(self) -> str:
        """NYSE chip state, memoized: the stream ticks at 10 Hz but the calendar
        math only reruns every 30s."""
        now = time.monotonic()
        last = self._market_status_ts
        if last is not None and now - last < _MARKET_STATUS_TTL_S:
            return self._market_status_cache
        self._market_status_ts = now
        try:
            self._market_status_cache = self._market_status_fn()
        except Exception:
            self._market_status_cache = ""
        return self._market_status_cache

    async def _build_focus(self, symbol: str) -> FocusView:
        quote = self.engine.quote(symbol)
        rng = self.engine.session_range(symbol)
        asset = "CRYPTO" if ":" in symbol else ("EQUITY" if self.source == "live" else "SIM")
        candles = [(b.t, b.o, b.h, b.l, b.c, b.vol) for b in self._focus_candles.bars()]
        depth = None
        if asset == "EQUITY" and self.cfg.show_depth:
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
            candles=candles, depth=depth,
            fundamentals=self._focus_fundamentals(symbol, asset),
            interval=self._chart_interval, timeframe=self.cfg.timeframe,
        )

    def _focus_fundamentals(self, symbol: str, asset: str) -> Fundamentals | None:
        """Cached fundamentals for a live bare ticker; a miss kicks one fetch.

        Gated to live equities and rate-limited to one request per symbol per
        TTL, with an in-flight marker so the 10 Hz stream cannot stampede it.
        """
        if asset != "EQUITY" or not self.cfg.enable_equities:
            return None
        entry = self._fundamentals_cache.get(symbol)
        if entry is not None and time.monotonic() - entry[0] < FUNDAMENTALS_TTL_S:
            return entry[1]
        if self._fundamentals_inflight != symbol:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return None
            self._fundamentals_inflight = symbol
            self._spawn("fundamentals", self._fetch_fundamentals(symbol))
        return None

    async def _fetch_fundamentals(self, symbol: str) -> None:
        """Silent by contract: a scrape hiccup caches None, which also rate-
        limits the retry to one per TTL."""
        data = None
        try:
            raw = await self._fundamentals_fetcher(symbol)
        except asyncio.CancelledError:
            # Superseded by a newer symbol's fetch: write nothing, and only
            # clear the marker if it is still ours.
            if self._fundamentals_inflight == symbol:
                self._fundamentals_inflight = None
            raise
        except Exception:
            log.debug("fundamentals fetch failed for %s", symbol, exc_info=True)
            raw = None
        if raw is not None:
            data = Fundamentals(pe=raw.pe, market_cap=raw.market_cap,
                                high_52w=raw.high_52w, low_52w=raw.low_52w)
        if self._fundamentals_inflight == symbol:
            self._fundamentals_inflight = None
        self._fundamentals_cache[symbol] = (time.monotonic(), data)
