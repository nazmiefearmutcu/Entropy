from __future__ import annotations

import msgspec

from .risk.profiles import RiskProfile, get_profile
from .strategies.base import Strategy
from .strategies.consensus import ConsensusStrategy
from .strategies.ema_cross import EmaCrossStrategy
from .strategies.momentum_scalper import MomentumScalper


class LiveConfig(msgspec.Struct, frozen=True):
    enabled: bool = False
    acknowledged_risk: bool = False
    exchange: str = "binance"
    api_key: str = ""
    api_secret: str = ""


class BotConfig(msgspec.Struct, frozen=True):
    mode: str = "paper"  # "paper" | "live"
    risk_profile: str = "medium"
    strategies: tuple[str, ...] = ("consensus", "ema_cross")
    symbols: tuple[str, ...] = ()  # () = all symbols from the feed
    starting_cash: float = 100_000.0
    fee_bps: float = 1.0
    slippage_bps: float = 1.0
    ema_symbol: str = "SPY"  # deterministic sim symbol by default; use "binance-spot:BTCUSDT" live
    ema_fast: int = 9
    ema_slow: int = 21
    momentum_min_pct: float = 0.15
    seed: int = 42
    equity_tps: int = 4000
    enable_crypto: bool = True
    enable_equities: bool = True
    console_log_path: str = "entropy_console.log"
    trade_csv_path: str = "entropy_trades.csv"
    live: LiveConfig = msgspec.field(default_factory=LiveConfig)

    def profile(self) -> RiskProfile:
        return get_profile(self.risk_profile)


def build_strategies(cfg: BotConfig) -> list[Strategy]:
    syms = cfg.symbols or None
    out: list[Strategy] = []
    for name in cfg.strategies:
        if name == "consensus":
            out.append(ConsensusStrategy(symbols=syms))
        elif name == "momentum_scalper":
            out.append(MomentumScalper(symbols=syms, min_pct=cfg.momentum_min_pct))
        elif name == "ema_cross":
            out.append(
                EmaCrossStrategy(symbol=cfg.ema_symbol, fast=cfg.ema_fast, slow=cfg.ema_slow)
            )
        else:
            raise KeyError(f"Unknown strategy {name!r}")
    return out
