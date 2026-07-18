from __future__ import annotations

import msgspec

from entropy.config import EngineConfig


class AppConfig(msgspec.Struct, frozen=True):
    seed: int = 42
    equity_tps: int = 4000
    enable_crypto: bool = True
    enable_equities: bool = True
    equity_source: str = "auto"  # "sim" | "live" | "auto" (live iff US market open)
    strategy_symbol: str = "SPY"
    crypto_strategy_symbol: str = "binance-spot:BTCUSDT"
    theme: str = "entropy"
    chart_type: str = "candlestick"
    show_volume: bool = True
    # Market-depth (DOM) ladder for the focus symbol. Hidden by default (the
    # `:depth` command toggles it); bins/top_n tune the synthetic VAP ladder.
    show_depth: bool = False
    depth_bins: int = 40
    depth_top_n: int = 6
    risk_profile: str = "medium"
    console_log_path: str = "entropy_console.log"
    trade_csv_path: str = "entropy_trades.csv"
    timeframe: str = "15m"
    # Persistent watchlist location; empty selects ~/.entropy/watchlist.json
    # (kept injectable so tests point it at a tmp path).
    watchlist_path: str = ""
    engine: EngineConfig = msgspec.field(default_factory=EngineConfig)
