"""validate(): NaN/inf rejection and the cost-aware stop-distance guard."""

from __future__ import annotations

import math

from entropy.bot.config import BotConfig, MarketCostConfig, validate, warnings


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


def test_validate_partial_cost_overrun_is_warning_not_error():
    # Frosty + defaults: crypto spot is dead (0.52) but equities (0.16) and
    # crypto futures (0.28) still trade, so validate() stays clean and
    # warnings() names the offending market with its ratio.
    assert validate(BotConfig(risk_profile="frosty")) == []
    notes = warnings(BotConfig(risk_profile="frosty"))
    assert any(
        "Frosty" in n and "crypto spot" in n and "0.52" in n
        and "max_cost_to_stop=0.5" in n
        for n in notes
    )
    assert not any("equities" in n for n in notes)


def test_validate_every_active_market_dead_is_hard_error():
    # max_cost_to_stop below every active market's ratio (0.1 < 0.16/0.28/0.52)
    # makes the whole entry set dead -> validate() must refuse the config and
    # warnings() stays silent (there is nothing left to warn about).
    problems = validate(BotConfig(risk_profile="frosty", max_cost_to_stop=0.1))
    assert any(
        "every active market" in p and "max_cost_to_stop=0.1" in p
        for p in problems
    )
    assert warnings(BotConfig(risk_profile="frosty", max_cost_to_stop=0.1)) == []


def test_validate_cost_aware_off_and_defaults_stay_clean():
    # cost_aware=False skips the guard entirely; defaults stay valid.
    assert validate(BotConfig(risk_profile="frosty", cost_aware=False)) == []
    assert warnings(BotConfig(risk_profile="frosty", cost_aware=False)) == []
    assert validate(BotConfig()) == []
    assert warnings(BotConfig()) == []


def test_validate_max_cost_to_stop_boundary_is_strict():
    # Frosty's ratio is exactly 0.52 (26 bps round trip / 50 bps stop):
    # 0.51 still warns, 0.52 and above are clean because the guard compares
    # with a strict ">".
    assert validate(BotConfig(risk_profile="frosty", max_cost_to_stop=0.51)) == []
    assert any(
        "max_cost_to_stop=0.51" in n
        for n in warnings(BotConfig(risk_profile="frosty", max_cost_to_stop=0.51))
    )
    assert warnings(BotConfig(risk_profile="frosty", max_cost_to_stop=0.52)) == []
    assert warnings(BotConfig(risk_profile="frosty", max_cost_to_stop=0.53)) == []


def test_validate_guard_blocks_only_the_offending_market():
    # Frosty + crypto spot is dead (0.52) while equities (0.16) and crypto
    # futures (0.28) still trade: disabling crypto must leave the config fully
    # clean, and the warning must name only the offending market.
    assert validate(BotConfig(risk_profile="frosty", enable_crypto=False)) == []
    assert warnings(BotConfig(risk_profile="frosty", enable_crypto=False)) == []
    notes = warnings(BotConfig(risk_profile="frosty", enable_equities=False))
    assert any("crypto spot" in n for n in notes)
    assert not any("equities" in n for n in notes)
    # Crypto-only with every crypto market dead -> hard error again.
    problems = validate(BotConfig(
        risk_profile="frosty", enable_equities=False, max_cost_to_stop=0.1
    ))
    assert any("every active market" in p for p in problems)
