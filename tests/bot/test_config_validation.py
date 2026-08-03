"""validate(): NaN/inf rejection and the cost-aware stop-distance guard."""

from __future__ import annotations

import math

from entropy.bot.config import BotConfig, MarketCostConfig, validate


def test_validate_rejects_non_finite_flat_costs():
    assert any("finite" in p for p in validate(BotConfig(fee_bps=math.nan)))
    assert any("finite" in p for p in validate(BotConfig(slippage_bps=math.inf)))
    assert any("finite" in p for p in validate(BotConfig(cost_edge_mult=math.nan)))
    assert any("finite" in p for p in validate(BotConfig(max_cost_to_stop=math.inf)))


def test_validate_rejects_non_finite_market_costs():
    problems = validate(BotConfig(
        market_costs=MarketCostConfig(crypto_spot_fee_bps=math.nan)
    ))
    assert any("finite" in p and "crypto spot fee" in p for p in problems)
    problems = validate(BotConfig(
        market_costs=MarketCostConfig(crypto_futures_slippage_bps=math.inf)
    ))
    assert any("finite" in p and "crypto futures slippage" in p for p in problems)


def test_validate_flags_cost_to_stop_overrun_for_tight_stops():
    # Frosty 0.5% stop vs crypto spot 26 bps round trip: 26/50 = 0.52 > 0.5,
    # so every cost-aware entry would be rejected by the risk layer.
    problems = validate(BotConfig(risk_profile="frosty"))
    assert any(
        "Frosty" in p and "crypto spot" in p and "0.52" in p
        and "max_cost_to_stop=0.5" in p
        for p in problems
    )


def test_validate_cost_aware_off_and_defaults_stay_clean():
    # cost_aware=False skips the guard entirely; defaults stay valid.
    assert validate(BotConfig(risk_profile="frosty", cost_aware=False)) == []
    assert validate(BotConfig()) == []


def test_validate_max_cost_to_stop_boundary_is_strict():
    # Frosty's ratio is exactly 0.52 (26 bps round trip / 50 bps stop):
    # 0.51 still blocks, 0.52 and above pass because the guard compares
    # with a strict ">".
    problems = validate(BotConfig(risk_profile="frosty", max_cost_to_stop=0.51))
    assert any("max_cost_to_stop=0.51" in p for p in problems)
    assert validate(BotConfig(risk_profile="frosty", max_cost_to_stop=0.52)) == []
    assert validate(BotConfig(risk_profile="frosty", max_cost_to_stop=0.53)) == []


def test_validate_guard_blocks_only_the_offending_market():
    # Frosty + crypto spot is dead (0.52) while equities (0.16) and crypto
    # futures (0.28) still trade: disabling crypto must leave the config
    # valid, and the problem must name the offending market.
    assert validate(BotConfig(risk_profile="frosty", enable_crypto=False)) == []
    problems = validate(BotConfig(risk_profile="frosty", enable_equities=False))
    assert any("crypto spot" in p for p in problems)
    assert not any("equities" in p for p in problems)
