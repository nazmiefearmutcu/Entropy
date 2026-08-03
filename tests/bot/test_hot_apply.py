import json
from pathlib import Path

import pytest

from entropy.bot.config import BotConfig, MarketCostConfig
from entropy.bot.runner import BotRunner

# Extreme's 2.0% stop leaves plenty of room under max_cost_to_stop for the
# default market costs, so the cost-aware validation never blocks these tests.
_BASE = BotConfig(risk_profile="extreme")


def test_cost_aware_toggle_updates_risk_and_executor_cost_models(tmp_path: Path):
    bot = BotRunner(BotConfig(risk_profile="extreme", cost_aware=False), run_dir=str(tmp_path))
    assert bot.risk.cost_model is None
    assert bot.executor.cost_model is None

    assert bot.apply_config(BotConfig(risk_profile="extreme", cost_aware=True)) == []

    assert bot.risk.cost_model is not None
    assert bot.executor.cost_model is not None
    assert bot.risk.cost_model.flat_fee_bps == 1.0
    assert bot.executor.cost_model.flat_fee_bps == 1.0


def test_market_costs_change_rebuilds_strategies_cold(tmp_path: Path):
    bot = BotRunner(_BASE, run_dir=str(tmp_path))
    bot.warm = True
    old_ids = [id(strat) for strat in bot.strategies]

    assert bot.apply_config(BotConfig(
        risk_profile="extreme", market_costs=MarketCostConfig(equity_fee_bps=5.0)
    )) == []

    assert [id(strat) for strat in bot.strategies] != old_ids
    assert bot.warm is False


def test_max_cost_to_stop_updates_risk_but_keeps_strategies(tmp_path: Path):
    bot = BotRunner(_BASE, run_dir=str(tmp_path))
    bot.warm = True
    old_strategies = bot.strategies

    assert bot.apply_config(BotConfig(risk_profile="extreme", max_cost_to_stop=0.8)) == []

    assert bot.risk.max_cost_to_stop == 0.8
    assert bot.strategies is old_strategies
    assert bot.warm is True


def test_hot_apply_preserves_risk_state(tmp_path: Path):
    bot = BotRunner(BotConfig(risk_profile="extreme", cost_aware=False), run_dir=str(tmp_path))
    bot.risk.trip()
    assert bot.risk.halted is True

    assert bot.apply_config(BotConfig(risk_profile="extreme", cost_aware=True)) == []

    assert bot.risk.halted is True
    assert bot.risk.circuit_tripped is True


def test_update_cost_model_called_only_when_cost_fields_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bot = BotRunner(_BASE, run_dir=str(tmp_path))
    calls: list[tuple[object | None, float]] = []
    monkeypatch.setattr(
        bot.risk,
        "update_cost_model",
        lambda cost_model, max_cost_to_stop: calls.append(
            (cost_model, max_cost_to_stop)
        ),
    )

    # Unrelated changes must not touch the risk cost layer.
    assert bot.apply_config(BotConfig(risk_profile="extreme", timeframe="4h")) == []
    assert calls == []

    assert bot.apply_config(BotConfig(risk_profile="extreme", max_cost_to_stop=0.25)) == []
    assert len(calls) == 1
    cost_model, max_cost_to_stop = calls[0]
    assert max_cost_to_stop == 0.25
    assert cost_model is not None


def test_config_applied_payload_tracks_cost_settings(tmp_path: Path):
    bot = BotRunner(BotConfig(risk_profile="extreme", cost_aware=False), run_dir=str(tmp_path))

    assert bot.apply_config(BotConfig(
        risk_profile="extreme", cost_aware=True, cost_edge_mult=3.0
    )) == []

    events = [
        json.loads(line)
        for line in (Path(tmp_path) / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    applied = [ev for ev in events if ev["kind"] == "config_applied"]
    assert applied and applied[-1]["cost_aware"] is True
    assert applied[-1]["cost_edge_mult"] == 3.0
