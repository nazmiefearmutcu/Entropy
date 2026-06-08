from __future__ import annotations

import msgspec

from entropy.config import EngineConfig


class AppConfig(msgspec.Struct, frozen=True):
    seed: int = 42
    equity_tps: int = 4000
    enable_crypto: bool = True
    enable_equities: bool = True
    strategy_symbol: str = "SPY"
    crypto_strategy_symbol: str = "binance-spot:BTCUSDT"
    engine: EngineConfig = msgspec.field(default_factory=EngineConfig)
