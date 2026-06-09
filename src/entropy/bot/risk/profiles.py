from __future__ import annotations

import msgspec


class RiskProfile(msgspec.Struct, frozen=True):
    name: str
    color: str  # rich/textual color name
    per_trade_pct: float  # % of equity allocated per trade
    max_concurrent: int  # max simultaneous open positions
    stop_loss_pct: float  # adverse move that closes a position
    take_profit_pct: float  # favorable move that closes a position
    max_total_exposure_pct: float  # cap on gross notional / equity
    max_daily_loss_pct: float  # daily-loss kill-switch threshold
    cooldown_s: float  # per-symbol re-entry cooldown
    description: str  # plain-English statement of how much risk this takes


CONSERVATIVE = RiskProfile(
    name="Conservative", color="green",
    per_trade_pct=1.0, max_concurrent=2, stop_loss_pct=0.5, take_profit_pct=1.0,
    max_total_exposure_pct=5.0, max_daily_loss_pct=2.0, cooldown_s=30.0,
    description=(
        "Conservative: allocates 1% of equity per trade, at most 2 open positions, "
        "0.5% stop / 1% target, up to 5% total exposure; halts all trading after a 2% daily loss."
    ),
)

BALANCED = RiskProfile(
    name="Balanced", color="yellow",
    per_trade_pct=2.5, max_concurrent=4, stop_loss_pct=1.0, take_profit_pct=2.0,
    max_total_exposure_pct=15.0, max_daily_loss_pct=5.0, cooldown_s=10.0,
    description=(
        "Balanced: allocates 2.5% of equity per trade, up to 4 open positions, "
        "1% stop / 2% target, up to 15% total exposure; halts all trading after a 5% daily loss."
    ),
)

AGGRESSIVE = RiskProfile(
    name="Aggressive", color="red",
    per_trade_pct=5.0, max_concurrent=8, stop_loss_pct=2.0, take_profit_pct=4.0,
    max_total_exposure_pct=40.0, max_daily_loss_pct=10.0, cooldown_s=2.0,
    description=(
        "Aggressive: allocates 5% of equity per trade, up to 8 open positions, "
        "2% stop / 4% target, up to 40% total exposure; halts all trading after a 10% daily loss."
    ),
)

PRESETS: dict[str, RiskProfile] = {
    p.name.lower(): p for p in (CONSERVATIVE, BALANCED, AGGRESSIVE)
}


def get_profile(name: str) -> RiskProfile:
    key = name.lower()
    if key not in PRESETS:
        raise KeyError(
            f"Unknown risk profile {name!r}; choose from {sorted(PRESETS)} or use make_custom()."
        )
    return PRESETS[key]


def make_custom(**overrides: object) -> RiskProfile:
    """Build a Custom profile by overriding Balanced's fields (e.g. per_trade_pct=3.0)."""
    return msgspec.structs.replace(BALANCED, name="Custom", color="cyan", **overrides)
