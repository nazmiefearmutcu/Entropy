from __future__ import annotations

import time

from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner
from entropy.engine.candles import CandleAggregator
from entropy.engine.engine import Engine
from entropy.feeds.equities.universe import UNIVERSE


class SpeedBenchmark:
    @staticmethod
    def run_engine_throughput(n_ticks: int = 250_000) -> float:
        """Measure Engine.on_trade ticks/sec throughput."""
        engine = Engine()
        symbols = UNIVERSE
        base_ts = 1_000_000_000_000
        
        t0 = time.perf_counter()
        for i in range(n_ticks):
            sym = symbols[i % len(symbols)]
            # Call on_trade
            engine.on_trade(
                sym,
                100.0 + (i % 17) * 0.1,
                10.0,
                "buy" if i & 1 else "sell",
                base_ts + i * 1000
            )
        dt = time.perf_counter() - t0
        return n_ticks / dt

    @staticmethod
    def run_candle_aggregator(n_ticks: int = 500_000) -> float:
        """Measure CandleAggregator performance."""
        agg = CandleAggregator(1_000_000_000) # 1s window
        t0 = time.perf_counter()
        for i in range(n_ticks):
            agg.add(i * 10_000_000, 100.0 + (i % 10), 1.0)
        dt = time.perf_counter() - t0
        return n_ticks / dt

    @staticmethod
    def run_full_bot_pipeline(n_ticks: int = 100_000) -> float:
        """Measure complete pipeline throughput: Engine -> Strategy -> Portfolio -> Risk Manager."""
        cfg = BotConfig(
            mode="paper",
            risk_profile="medium",
            strategies=("momentum_scalper", "ema_cross"),
            symbols=("AAPL", "MSFT", "NVDA", "SPY"),
            ema_symbol="SPY",
            starting_cash=100_000.0,
            enable_crypto=False,
            enable_equities=False
        )
        runner = BotRunner(cfg)
        # Prevent disk writes
        from entropy.bot.calibration import DummyLedger
        runner.ledger = DummyLedger() # type: ignore
        
        symbols = ["AAPL", "MSFT", "NVDA", "SPY"]
        base_ts = 1_700_000_000_000_000_000
        
        t0 = time.perf_counter()
        for i in range(n_ticks):
            sym = symbols[i % len(symbols)]
            runner.on_trade(
                sym,
                150.0 + (i % 25) * 0.05,
                5.0,
                "buy" if i % 3 == 0 else "sell",
                base_ts + i * 1_000_000
            )
        dt = time.perf_counter() - t0
        return n_ticks / dt
