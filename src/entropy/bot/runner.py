from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque

import msgspec
from crocodile.core.schema.records import Trade

from entropy.config import EngineConfig
from entropy.engine.engine import Engine
from entropy.engine.timeframe import CHART_INTERVALS, get_timeframe
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed
from entropy.strategy.engine import Bar

from .config import BotConfig, build_strategies
from .execution.base import ExecutionAdapter
from .execution.live import LiveExecutor, LiveTradingDisabledError
from .execution.paper import PaperExecutor
from .ledger import Ledger
from .orders import Order, OrderIntent, OrderSide
from .portfolio import Portfolio, PortfolioSnapshot, PositionSide
from .risk.manager import RiskManager
from .risk.profiles import RiskProfile, get_profile
from .signals import SignalAction

log = logging.getLogger(__name__)

_NS_PER_S = 1_000_000_000


class StrategyView(msgspec.Struct, frozen=True):
    """Per-strategy telemetry for the dashboards.

    Without this the UIs could only show P&L, so "is the bot even looking at
    anything?" was unanswerable — a live bot and a stalled one looked identical.
    """

    name: str
    warm: bool
    #: symbol -> "trend" | "range" | "chop" (consensus only; empty otherwise)
    regimes: dict[str, str] = msgspec.field(default_factory=dict)
    #: symbol -> +1 long / -1 short / 0 flat, as tracked by the strategy itself
    directions: dict[str, int] = msgspec.field(default_factory=dict)


class BotSnapshot(msgspec.Struct, frozen=True):
    portfolio: PortfolioSnapshot
    risk_profile: RiskProfile
    halted: bool
    ticks: int
    mode: str = "paper"
    timeframe: str = "1m"
    bar_s: float = 0.0
    warm: bool = False
    strategies: tuple[StrategyView, ...] = ()
    last_signals: tuple[str, ...] = ()
    last_rejects: tuple[str, ...] = ()


def _make_executor(cfg: BotConfig) -> ExecutionAdapter:
    if cfg.mode == "live":
        return LiveExecutor(enabled=cfg.live.enabled, acknowledged_risk=cfg.live.acknowledged_risk,
                            api_key=cfg.live.api_key, api_secret=cfg.live.api_secret)
    return PaperExecutor(
        fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps,
        cost_model=cfg.cost_model(),
    )


#: Ring size for the "what has the bot done lately" feeds the dashboards read.
_RECENT_MAX = 40

#: Startup warmup is best-effort; a stalled provider must not hold the feeds shut.
_WARMUP_TIMEOUT_S = 20.0

#: ``Order.strategy`` values the RISK layer stamps on closes it originated —
#: RiskManager.check_exits uses "risk", close_all_positions "circuit_breaker".
_RISK_ORIGINATED = frozenset({"risk", "circuit_breaker"})


class BotRunner:
    def __init__(self, config: BotConfig, run_dir: str = "runs/latest",
                 equity_record_period_s: float = 1.0) -> None:
        self.config = config
        self._equity_record_period_s = equity_record_period_s
        # The bot's scanner runs on the CONFIGURED timeframe. This used to be a
        # bare Engine(), i.e. permanently pinned to the legacy 30s/1m/5m windows
        # no matter what cadence the strategies were asked to trade on.
        self.engine = Engine(EngineConfig.from_timeframe(get_timeframe(config.timeframe)))
        self.portfolio = Portfolio(config.starting_cash)
        self.risk = RiskManager(
            config.profile(),
            cost_model=config.cost_model(),
            max_cost_to_stop=config.max_cost_to_stop,
        )
        self.executor = _make_executor(config)
        self.strategies = build_strategies(config)
        self.ledger = Ledger(run_dir, mode=config.mode, trade_csv_path=config.trade_csv_path)
        self._sink = QueueSink()
        self._equity = EquitySimFeed(self._sink, seed=config.seed, ticks_per_sec=config.equity_tps)
        self.ticks = 0
        self._last_ts_ns = 0
        self._utc_day = time.strftime("%Y-%m-%d", time.gmtime())
        self.warm = False
        self.paused = False
        self._recent_signals: deque[str] = deque(maxlen=_RECENT_MAX)
        self._recent_rejects: deque[str] = deque(maxlen=_RECENT_MAX)

    # ---- synchronous hot path -------------------------------------------------
    def on_trade(self, symbol: str, price: float, amount: float, side: str, ts_ns: int) -> None:
        self.risk.update_tick(symbol, price, ts_ns)
        events = self.engine.on_trade(symbol, price, amount, side, ts_ns)
        self.portfolio.mark(symbol, price)
        self._last_ts_ns = ts_ns
        self.ticks += 1
        # mechanical stop/take-profit exits first. These run even while paused:
        # pausing stops the bot taking NEW risk, it must never strand an open
        # position without its stop.
        for order in self.risk.check_exits(self.portfolio, ts_ns):
            self._execute(order)
        # strategy signals
        for strat in self.strategies:
            for sig in strat.on_tick(symbol, price, ts_ns, events):
                self._recent_signals.append(
                    f"{sig.strategy} {sig.action.value} {sig.symbol} @{price:.4g} — {sig.reason}"
                )
                if self.paused and sig.action is not SignalAction.EXIT:
                    self._recent_rejects.append(f"{sig.symbol}: paused")
                    self._notify_closed(sig.symbol, "paused")
                    continue
                decision = self.risk.evaluate(sig, self.portfolio, price, ts_ns)
                if decision.approved and decision.order is not None:
                    self._execute(decision.order)
                elif not decision.approved:
                    self._recent_rejects.append(f"{sig.symbol}: {decision.reason}")
                    self.ledger.record_reject(sig.symbol, decision.reason)
                    if sig.action is not SignalAction.EXIT:
                        # The entry never happened, so the strategy must not go on
                        # believing it holds the position it just asked for.
                        self._notify_closed(sig.symbol, f"rejected: {decision.reason}")

    def _execute(self, order: Order) -> None:
        try:
            fill = self.executor.submit(order)
        except (LiveTradingDisabledError, NotImplementedError) as exc:
            # Live execution is guarded / intentionally unimplemented. Record the block
            # HONESTLY and do NOT fabricate a fill or mutate the portfolio — a blocked
            # order must never look like a real one.
            self.ledger.record_event("live_blocked", {
                "symbol": order.symbol, "intent": order.intent.value,
                "reason": str(exc).splitlines()[0],
            })
            return
        if order.intent is OrderIntent.OPEN:
            pos_side = PositionSide.LONG if order.side is OrderSide.BUY else PositionSide.SHORT
            stop_px, tp_px = self.risk.stop_tp_prices(pos_side, fill.price, order.symbol)
            self.portfolio.open(order.symbol, pos_side, fill.qty, fill.price,
                                stop_px, tp_px, fill.ts_ns, fill.fee)
            self.ledger.record_trade_open(
                order.symbol,
                "LONG" if pos_side is PositionSide.LONG else "SHORT",
                fill.price,
            )
        else:
            pos = self.portfolio.positions.get(order.symbol)
            side_str = "LONG"
            if pos is not None:
                side_str = "LONG" if pos.side is PositionSide.LONG else "SHORT"
            else:
                side_str = "LONG" if order.side is OrderSide.SELL else "SHORT"
            self.portfolio.close(order.symbol, fill.price, fill.ts_ns, fill.fee)
            self.ledger.record_trade_close(order.symbol, side_str, fill.price)
            if order.strategy in _RISK_ORIGINATED:
                # A stop, a take-profit or the circuit breaker closed this —
                # the strategy did not ask and has no idea it is flat again.
                # Strategy-requested exits are deliberately NOT notified: a
                # reversal emits EXIT and ENTER_SHORT from the same tick, and
                # the strategy has already recorded the new side internally by
                # the time the runner works through that pair.
                self._notify_closed(order.symbol, order.intent.value)
        self.ledger.record_fill(fill, order.intent)

    def _notify_closed(self, symbol: str, reason: str) -> None:
        """Re-arm every strategy that thinks it still holds ``symbol``.

        Defensive getattr: the Strategy protocol is structural, so a strategy
        from outside this package may predate the hook.
        """
        for strat in self.strategies:
            hook = getattr(strat, "on_position_closed", None)
            if hook is not None:
                hook(symbol, reason)

    def trip_circuit_breaker(self) -> None:
        self.risk.trip()
        ts_ns = self._last_ts_ns if self._last_ts_ns > 0 else int(time.time() * _NS_PER_S)
        self.ledger.record_event("emergency_halt", {"timestamp": ts_ns})
        orders = self.risk.close_all_positions(self.portfolio, ts_ns)
        for order in orders:
            self._execute(order)

    # ---- control --------------------------------------------------------------
    def set_risk_profile(self, name: str) -> RiskProfile:
        old = self.risk.profile.name
        profile = get_profile(name)
        self.risk.set_profile(profile)
        self.ledger.record_risk_change(old, profile.name)
        return profile

    def apply_config(self, cfg: BotConfig) -> list[str]:
        """Hot-apply a new BotConfig; returns validation problems (empty = applied).

        Deliberately partial. Risk, strategies, thresholds and the scanner
        cadence all re-derive in place, but ``starting_cash`` and ``mode`` are
        NOT re-read: rebasing the account or swapping paper for live under a
        running portfolio would silently invalidate every open position and the
        ledger's P&L history. Those need a restart, and the UIs say so.
        """
        from .config import validate, warnings
        problems = validate(cfg)
        if problems:
            return problems

        tf_changed = cfg.timeframe != self.config.timeframe
        bar_changed = cfg.bar_seconds() != self.config.bar_seconds()
        strat_changed = (
            cfg.strategies != self.config.strategies
            or cfg.consensus != self.config.consensus
            or cfg.symbols != self.config.symbols
            or (cfg.ema_symbol, cfg.ema_fast, cfg.ema_slow)
            != (self.config.ema_symbol, self.config.ema_fast, self.config.ema_slow)
            or cfg.momentum_min_pct != self.config.momentum_min_pct
            or cfg.cost_aware != self.config.cost_aware
            or cfg.market_costs != self.config.market_costs
            or cfg.cost_edge_mult != self.config.cost_edge_mult
            or cfg.fee_bps != self.config.fee_bps
            or cfg.slippage_bps != self.config.slippage_bps
        )
        risk_cost_changed = (
            cfg.cost_aware != self.config.cost_aware
            or cfg.market_costs != self.config.market_costs
            or cfg.fee_bps != self.config.fee_bps
            or cfg.slippage_bps != self.config.slippage_bps
            or cfg.max_cost_to_stop != self.config.max_cost_to_stop
        )
        old_profile = self.risk.profile.name

        self.config = cfg
        profile = cfg.profile()
        if profile != self.risk.profile:
            self.risk.set_profile(profile)
            self.ledger.record_risk_change(old_profile, profile.name)
        self.executor = _make_executor(cfg)
        if risk_cost_changed:
            # Swap the risk layer's cost model in place. A full rebuild would
            # drop cooldown timers, tick history and halt flags; this only
            # replaces the cost fields so live state survives the hot-apply.
            self.risk.update_cost_model(cfg.cost_model(), cfg.max_cost_to_stop)

        if tf_changed:
            self.engine = Engine(EngineConfig.from_timeframe(get_timeframe(cfg.timeframe)))
        if strat_changed or bar_changed:
            # Rebuilt cold: mutating periods under a warm indicator series would
            # mix values computed with the old parameters into the new ones.
            self.strategies = build_strategies(cfg)
            self.warm = False
        self.ledger.record_event("config_applied", {
            "timeframe": cfg.timeframe, "bar_s": cfg.bar_seconds(),
            "strategies": list(cfg.strategies), "risk": profile.name,
            "vote_mode": cfg.consensus.vote_mode,
            "cost_aware": cfg.cost_aware, "cost_edge_mult": cfg.cost_edge_mult,
            "max_cost_to_stop": cfg.max_cost_to_stop,
            "fee_bps": cfg.fee_bps, "slippage_bps": cfg.slippage_bps,
            "market_costs": {
                k.value: {"fee_bps": v.fee_bps, "slippage_bps": v.slippage_bps}
                for k, v in cfg.market_costs.as_mapping().items()
            },
            "warnings": warnings(cfg),
        })
        return []

    def set_paused(self, paused: bool) -> None:
        """Stop/resume taking NEW positions. Exits keep working either way."""
        if paused == self.paused:
            return
        self.paused = paused
        self.ledger.record_event("paused" if paused else "resumed", {"ticks": self.ticks})

    def _strategy_views(self) -> tuple[StrategyView, ...]:
        out: list[StrategyView] = []
        for strat in self.strategies:
            regimes: dict[str, str] = {}
            directions: dict[str, int] = {}
            # Only ConsensusStrategy publishes regime/direction state; the others
            # are duck-typed away rather than isinstance-checked so a future
            # strategy can opt in just by exposing the same attributes.
            for sym, regime in getattr(strat, "last_regime", {}).items():
                regimes[sym] = regime.label
            for sym, st in getattr(strat, "_states", {}).items():
                directions[sym] = getattr(st, "direction", 0)
            core = getattr(strat, "_core", None)
            warm = bool(
                getattr(strat, "_states", None)
                or (core is not None and getattr(core, "is_warm", False))
            )
            out.append(StrategyView(
                name=strat.name, warm=warm, regimes=regimes, directions=directions
            ))
        return tuple(out)

    def snapshot(self) -> BotSnapshot:
        return BotSnapshot(
            portfolio=self.portfolio.snapshot(self._last_ts_ns),
            risk_profile=self.risk.profile, halted=self.risk.halted, ticks=self.ticks,
            mode=self.config.mode, timeframe=self.config.timeframe,
            bar_s=self.config.bar_seconds(), warm=self.warm,
            strategies=self._strategy_views(),
            last_signals=tuple(self._recent_signals),
            last_rejects=tuple(self._recent_rejects),
        )

    # ---- warmup ---------------------------------------------------------------

    async def warmup(self) -> bool:
        """Seed the strategies with real history so they can signal immediately.

        Without this a fresh bot must accumulate ``min_bars`` live bars first —
        35 minutes on a 1m cadence, nearly 9 hours on 15m — during which it looks
        broken rather than warming. Best-effort: a failure logs and leaves the
        bot to warm from the live tape as before.

        Warmup is SKIPPED in two cases, both because a wrong seed is worse than
        no seed:

        * the strategy bar length has no matching provider interval — seeding
          1m history into a 30s-bar strategy hands the indicators a cadence they
          will never see live;
        * the symbol's feed is the SIMULATOR. ``BotRunner`` only ever runs
          :class:`EquitySimFeed` for equities, so real Yahoo bars would be
          spliced onto synthetic ~$100 ticks — a price discontinuity that would
          instantly fire a bogus cross and journal a phantom trade.
        """
        if not self.config.warmup:
            return False
        symbol = self.config.ema_symbol
        if ":" not in symbol:
            log.info("bot warmup skipped for %s: equities run on the simulator, "
                     "so real history would not match the tape", symbol)
            return False
        if not self.config.enable_crypto:
            return False
        interval = self._provider_interval()
        if interval is None:
            log.info("bot warmup skipped: no provider interval for %.1fs bars",
                     self.config.bar_seconds())
            return False
        try:
            bars = await asyncio.wait_for(
                self._fetch_warmup_bars(symbol, interval), timeout=_WARMUP_TIMEOUT_S
            )
        except Exception as exc:  # network/REST/timeout — warmup is best-effort.
            log.info("bot warmup failed for %s (%s); warming from the live tape",
                     symbol, exc)
            self.ledger.record_event("warmup_failed", {"symbol": symbol,
                                                       "reason": str(exc)[:200]})
            return False
        if not bars:
            return False
        for strat in self.strategies:
            strat.warmup(bars)
        self.warm = True
        self.ledger.record_event("warmup", {"symbol": symbol, "interval": interval,
                                            "bars": len(bars)})
        return True

    def _provider_interval(self) -> str | None:
        """Interval name whose bar EXACTLY matches the strategy bar, else None."""
        want_ns = int(self.config.bar_seconds() * _NS_PER_S)
        for name, bar_ns in CHART_INTERVALS.items():
            if bar_ns == want_ns:
                return name
        return None

    async def _fetch_warmup_bars(self, symbol: str, interval: str) -> list[Bar]:
        from entropy.engine.timeframe import chart_warmup_interval
        from entropy.feeds.warmup import warmup_equity_bars, warmup_klines

        crypto = ":" in symbol
        usable = chart_warmup_interval(interval, crypto=crypto)
        if usable is None:
            return []
        if crypto:
            venue, raw = symbol.split(":", 1)
            if venue != "binance-spot":
                return []   # only Binance serves klines here — not an error
            return await warmup_klines(raw, interval=usable)
        return await warmup_equity_bars(symbol, usable)

    # ---- async wiring ---------------------------------------------------------
    async def _drain(self) -> None:
        q = self._sink.q
        while True:
            r = await q.get()
            if isinstance(r, Trade):
                self.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)

    def _maybe_rollover_day(self) -> None:
        """On a UTC date change, reset the daily baseline + clear the kill-switch so the
        daily-loss limit is genuinely per-day (not cumulative-since-start)."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if day != self._utc_day:
            self._utc_day = day
            self.portfolio.reset_day()
            self.risk.reset_day()
            self.ledger.record_event("day_rollover", {"day": day})

    async def _record_equity_loop(self, period_s: float = 1.0) -> None:
        while True:
            await asyncio.sleep(period_s)
            self._maybe_rollover_day()
            self.ledger.record_equity(self.portfolio.snapshot(self._last_ts_ns))

    async def run(self) -> None:
        # Warm BEFORE the feeds open: seeding history onto a strategy that has
        # already started committing live bars would splice two cadences of
        # closes into one indicator series.
        await self.warmup()
        tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(self._drain()),
            asyncio.create_task(self._record_equity_loop(self._equity_record_period_s)),
        ]
        if self.config.enable_equities:
            tasks.append(asyncio.create_task(self._equity.run()))
        if self.config.enable_crypto:
            from entropy.feeds.crypto import start_feed
            tasks.append(await start_feed(self._sink))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
