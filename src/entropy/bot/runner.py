from __future__ import annotations

import asyncio
import contextlib

import msgspec
from crypcodile.schema.records import Trade

from entropy.engine.engine import Engine
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed

from .config import BotConfig, build_strategies
from .execution.base import ExecutionAdapter
from .execution.live import LiveExecutor
from .execution.paper import PaperExecutor
from .ledger import Ledger
from .orders import Order, OrderIntent, OrderSide
from .portfolio import Portfolio, PortfolioSnapshot, PositionSide
from .risk.manager import RiskManager
from .risk.profiles import RiskProfile, get_profile

_NS_PER_S = 1_000_000_000


class BotSnapshot(msgspec.Struct, frozen=True):
    portfolio: PortfolioSnapshot
    risk_profile: RiskProfile
    halted: bool
    ticks: int


def _make_executor(cfg: BotConfig) -> ExecutionAdapter:
    if cfg.mode == "live":
        return LiveExecutor(enabled=cfg.live.enabled, acknowledged_risk=cfg.live.acknowledged_risk,
                            api_key=cfg.live.api_key, api_secret=cfg.live.api_secret)
    return PaperExecutor(fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps)


class BotRunner:
    def __init__(self, config: BotConfig, run_dir: str = "runs/latest") -> None:
        self.config = config
        self.engine = Engine()
        self.portfolio = Portfolio(config.starting_cash)
        self.risk = RiskManager(config.profile())
        self.executor = _make_executor(config)
        self.strategies = build_strategies(config)
        self.ledger = Ledger(run_dir)
        self._sink = QueueSink()
        self._equity = EquitySimFeed(self._sink, seed=config.seed, ticks_per_sec=config.equity_tps)
        self.ticks = 0
        self._last_ts_ns = 0

    # ---- synchronous hot path -------------------------------------------------
    def on_trade(self, symbol: str, price: float, amount: float, side: str, ts_ns: int) -> None:
        events = self.engine.on_trade(symbol, price, amount, side, ts_ns)
        self.portfolio.mark(symbol, price)
        self._last_ts_ns = ts_ns
        self.ticks += 1
        # mechanical stop/take-profit exits first
        for order in self.risk.check_exits(self.portfolio, ts_ns):
            self._execute(order)
        # strategy signals
        for strat in self.strategies:
            for sig in strat.on_tick(symbol, price, ts_ns, events):
                decision = self.risk.evaluate(sig, self.portfolio, price, ts_ns)
                if decision.approved and decision.order is not None:
                    self._execute(decision.order)
                elif not decision.approved:
                    self.ledger.record_reject(sig.symbol, decision.reason)

    def _execute(self, order: Order) -> None:
        fill = self.executor.submit(order)
        if order.intent is OrderIntent.OPEN:
            pos_side = PositionSide.LONG if order.side is OrderSide.BUY else PositionSide.SHORT
            stop_px, tp_px = self.risk.stop_tp_prices(pos_side, fill.price)
            self.portfolio.open(order.symbol, pos_side, fill.qty, fill.price,
                                stop_px, tp_px, fill.ts_ns, fill.fee)
        else:
            self.portfolio.close(order.symbol, fill.price, fill.ts_ns, fill.fee)
        self.ledger.record_fill(fill, order.intent)

    # ---- control --------------------------------------------------------------
    def set_risk_profile(self, name: str) -> RiskProfile:
        old = self.risk.profile.name
        profile = get_profile(name)
        self.risk.set_profile(profile)
        self.ledger.record_risk_change(old, profile.name)
        return profile

    def snapshot(self) -> BotSnapshot:
        return BotSnapshot(
            portfolio=self.portfolio.snapshot(self._last_ts_ns),
            risk_profile=self.risk.profile, halted=self.risk.halted, ticks=self.ticks,
        )

    # ---- async wiring ---------------------------------------------------------
    async def _drain(self) -> None:
        q = self._sink.q
        while True:
            r = await q.get()
            if isinstance(r, Trade):
                self.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)

    async def _record_equity_loop(self, period_s: float = 1.0) -> None:
        while True:
            await asyncio.sleep(period_s)
            self.ledger.record_equity(self.portfolio.snapshot(self._last_ts_ns))

    async def run(self) -> None:
        tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(self._drain()),
            asyncio.create_task(self._record_equity_loop()),
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
