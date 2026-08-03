"""Cost model: per-market fee/slippage resolution and the cost-aware math.

The numbers pin the researched 2026-08-02 venue schedules: Binance spot
10.0/3.0 bps, Binance USDT-M futures 5.0/2.0 bps, US equities 2.0/2.0 bps.
"""

from __future__ import annotations

import pytest

from entropy.bot.config import BotConfig, MarketCostConfig, validate
from entropy.bot.costs import (
    E_ABS_MOVE,
    CostModel,
    MarketClass,
    MarketCosts,
    classify_symbol,
    fee_adjusted_kelly,
)


def test_classify_symbol():
    assert classify_symbol("AAPL") is MarketClass.EQUITY
    assert classify_symbol("SPY") is MarketClass.EQUITY
    assert classify_symbol("SOLUSDT") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("BTCUSDT") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("binance-spot:BTCUSDT") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("binance-futures:BTCUSDT") is MarketClass.CRYPTO_FUTURES
    assert classify_symbol("futures:ETHUSDT") is MarketClass.CRYPTO_FUTURES
    assert classify_symbol("coinbase:BTC-USD") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("coinbase-spot:ETH-USD") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("crypto:SOL-USD") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("BTC-USD") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("ETH-USDT") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("BTC-USDC") is MarketClass.CRYPTO_SPOT
    assert classify_symbol("BTC-PERP") is MarketClass.CRYPTO_FUTURES
    assert classify_symbol("ETH-SWAP") is MarketClass.CRYPTO_FUTURES
    assert classify_symbol("BTCUSDT_250926") is MarketClass.CRYPTO_FUTURES
    assert classify_symbol("UNKNOWNX") is MarketClass.EQUITY  # cheapest fallback


def test_round_trip_math():
    spot = MarketCosts(fee_bps=10.0, slippage_bps=3.0)
    assert spot.round_trip_bps == 26.0
    assert spot.breakeven_move == pytest.approx(26.0 / 10_000.0)
    assert spot.minimum_move(2.0) == pytest.approx(52.0 / 10_000.0)
    assert spot.sigma_gate(2.0) == pytest.approx(52.0 / 10_000.0 / E_ABS_MOVE)
    assert spot.cost_to_stop(1.0) == pytest.approx(0.26)  # 26 bps / 100 bps stop
    assert MarketCosts(2.0, 2.0).round_trip_bps == 8.0
    assert MarketCosts(5.0, 2.0).round_trip_bps == 14.0


def test_cost_model_resolution_and_partial_overrides():
    cm = CostModel(
        flat_fee_bps=1.0,
        flat_slippage_bps=1.0,
        market={MarketClass.CRYPTO_SPOT: MarketCosts(10.0, 3.0)},
    )
    assert cm.for_symbol("AAPL") == MarketCosts(1.0, 1.0)  # flat fallback
    assert cm.for_symbol("SOLUSDT") == MarketCosts(10.0, 3.0)
    assert cm.round_trip_bps("SOLUSDT") == 26.0

    # Partial override: fee set, slippage inherits the flat value.
    partial = CostModel(
        flat_fee_bps=1.0,
        flat_slippage_bps=2.0,
        market={MarketClass.EQUITY: MarketCosts(3.0, None)},
    )
    assert partial.for_symbol("AAPL") == MarketCosts(3.0, 2.0)


def test_bot_config_cost_model_defaults():
    cfg = BotConfig()
    cm = cfg.cost_model()
    assert cm.for_symbol("AAPL") == MarketCosts(2.0, 2.0)       # 8 bps round trip
    assert cm.for_symbol("SOLUSDT") == MarketCosts(10.0, 3.0)   # 26 bps round trip
    assert cm.for_symbol("binance-futures:BTCUSDT") == MarketCosts(5.0, 2.0)

    # Explicit flat fees win when the market table is cleared.
    flat = BotConfig(fee_bps=7.0, slippage_bps=1.0,
                     market_costs=MarketCostConfig.flat())
    assert flat.cost_model().for_symbol("SOLUSDT") == MarketCosts(7.0, 1.0)


def test_validate_accepts_defaults_and_rejects_bad_market_costs():
    assert validate(BotConfig()) == []
    bad = BotConfig(market_costs=MarketCostConfig(crypto_spot_fee_bps=-1.0))
    problems = validate(bad)
    assert any("crypto spot fee" in p for p in problems)
    assert any("cost edge multiplier" in p
               for p in validate(BotConfig(cost_edge_mult=0.0)))


def test_fee_adjusted_kelly():
    # Research example: p=0.55, b=1.5, L=50bps, C=14bps -> b_net=(1.5-0.14)/(1+0.14)
    b_net = (1.5 - 0.0014) / (1.0 + 0.0014)
    expected = (b_net * 0.55 - 0.45) / b_net
    assert fee_adjusted_kelly(0.55, 1.5, 0.0014) == pytest.approx(expected)
    # Costs larger than the gross win make the net payoff ratio negative -> 0.
    assert fee_adjusted_kelly(0.8, 1.0, 1.2) == 0.0
    # Zero/negative net edge clamps to 0 (never a negative fraction).
    assert fee_adjusted_kelly(0.5, 1.0, 0.0001) == 0.0
    assert fee_adjusted_kelly(0.4, 1.5, 0.001) == 0.0
    with pytest.raises(ValueError):
        fee_adjusted_kelly(0.0, 1.5, 0.001)
    with pytest.raises(ValueError):
        fee_adjusted_kelly(0.5, 0.0, 0.001)
