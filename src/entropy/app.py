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
    theme: str = "entropy"
    chart_type: str = "candlestick"
    show_volume: bool = True
    risk_profile: str = "medium"
    console_log_path: str = "entropy_console.log"
    trade_csv_path: str = "entropy_trades.csv"
    engine: EngineConfig = msgspec.field(default_factory=EngineConfig)
