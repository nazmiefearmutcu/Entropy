from __future__ import annotations

from typing import Any

import pytest

from entropy.bot.calibration import (
    DummyLedger,
    calibrate_and_test,
    run_backtest,
    split_folds,
    walk_forward,
)
from entropy.bot.orders import Fill, OrderIntent, OrderSide

# A 1-combo grid so walk-forward tests run one train + one eval backtest per fold.
TINY_GRID: dict[str, list[Any]] = {
    "fast": [9],
    "slow": [21],
    "min_pct": [0.15],
    "threshold": [0.5],
    "bar_s": [5.0],
    "stop_loss_pct": [1.0],
    "take_profit_pct": [2.0],
}


def test_calibrate_and_test() -> None:
    # Use small number of ticks to keep the test fast
    res = calibrate_and_test(n_ticks_back=200, n_ticks_forward=200, seed=123)
    
    assert "symbols" in res
    assert "best_params" in res
    assert "back_results" in res
    assert "forward_results" in res
    
    assert len(res["symbols"]["equities"]) == 3
    assert len(res["symbols"]["crypto"]) == 3
    
    # Check that best parameters keys exist
    assert "fast" in res["best_params"]
    assert "slow" in res["best_params"]
    
    # Check that back test and forward test returned results with expected structure
    for results in (res["back_results"], res["forward_results"]):
        assert "final_equity" in results
        assert "win_rate" in results
        assert "total_trades" in results
        assert "profit_factor" in results
        assert "sharpe" in results


def test_calibrate_and_test_fallback() -> None:
    # Use only 2 ticks so no trades are made, triggering the default fallback
    res = calibrate_and_test(n_ticks_back=2, n_ticks_forward=2, seed=123)
    assert res["best_params"] == {
        "fast": 9,
        "slow": 21,
        "min_pct": 0.15,
        "stop_loss_pct": 1.0,
        "take_profit_pct": 2.0
    }


def test_run_backtest_pnl_calculation(monkeypatch) -> None:
    # Predefined fills for LONG and SHORT trades
    # 1. LONG trade: entry buy at 100, exit sell at 110, qty 10, entry fee 1.5, exit fee 2.0
    # Expected gross PnL = (110 - 100) * 10 = 100. Net PnL = 100 - 1.5 - 2.0 = 96.5. (Win)
    fill_long_open = Fill(
        order_id="o1", symbol="SPY", side=OrderSide.BUY, qty=10.0, price=100.0, fee=1.5,
        slippage=0.0, ts_ns=1000,
    )
    fill_long_close = Fill(
        order_id="o2", symbol="SPY", side=OrderSide.SELL, qty=10.0, price=110.0, fee=2.0,
        slippage=0.0, ts_ns=2000,
    )
    
    # 2. SHORT trade: entry sell at 50, exit buy at 55, qty 5, entry fee 1.0, exit fee 1.0
    # Expected gross PnL = (50 - 55) * 5 = -25. Net PnL = -25 - 1.0 - 1.0 = -27.0. (Loss)
    fill_short_open = Fill(
        order_id="o3", symbol="AAPL", side=OrderSide.SELL, qty=5.0, price=50.0, fee=1.0,
        slippage=0.0, ts_ns=3000,
    )
    fill_short_close = Fill(
        order_id="o4", symbol="AAPL", side=OrderSide.BUY, qty=5.0, price=55.0, fee=1.0,
        slippage=0.0, ts_ns=4000,
    )
    
    predefined_fills = [
        (fill_long_open, OrderIntent.OPEN),
        (fill_long_close, OrderIntent.CLOSE),
        (fill_short_open, OrderIntent.OPEN),
        (fill_short_close, OrderIntent.CLOSE)
    ]
    
    class MockDummyLedger(DummyLedger):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fills = predefined_fills
            
    monkeypatch.setattr("entropy.bot.calibration.DummyLedger", MockDummyLedger)
    
    # Pass 1 dummy tick to avoid empty ticks edge case
    dummy_ticks = [{"symbol": "SPY", "price": 100.0, "amount": 1.0, "side": "buy", "ts_ns": 5000}]
    
    res = run_backtest(
        ticks=dummy_ticks,
        symbols=["SPY", "AAPL"],
        fast=9,
        slow=21,
        min_pct=0.15,
        stop_loss_pct=1.0,
        take_profit_pct=2.0
    )
    
    # Verify calculated metrics match expectations
    assert res["total_trades"] == 2
    assert res["closed_pnls"] == [96.5, -27.0]
    assert res["win_rate"] == 0.5
    # profit_factor = total_profit / total_loss = 96.5 / 27.0
    assert abs(res["profit_factor"] - (96.5 / 27.0)) < 1e-6


def test_end_of_run_liquidations_counted_in_trade_stats(monkeypatch) -> None:
    """A run that ends with an open winning position must count that position's
    PnL in closed_pnls / win_rate / total_trades — previously it was folded into
    final_equity only (portfolio.close bypassed the ledger), skewing every
    per-trade metric the grid search optimizes."""
    from entropy.bot.orders import Order
    from entropy.bot.runner import BotRunner

    class SeededRunner(BotRunner):
        """Opens one LONG through the real executor on the first tick, then just
        marks prices — guaranteeing exactly one still-open position at the end."""

        def on_trade(self, symbol, price, amount, side, ts_ns):  # type: ignore[override]
            self.portfolio.mark(symbol, price)
            if not self.portfolio.positions and not self.ticks:
                order = Order(id="t1", symbol=symbol, side=OrderSide.BUY,
                              intent=OrderIntent.OPEN, qty=10.0, price=price,
                              ts_ns=ts_ns, strategy="test")
                self._execute(order)
            self.ticks += 1

    monkeypatch.setattr("entropy.bot.calibration.BotRunner", SeededRunner)
    ticks = [
        {"symbol": "SPY", "price": 100.0, "amount": 1.0, "side": "buy", "ts_ns": 1000},
        {"symbol": "SPY", "price": 110.0, "amount": 1.0, "side": "buy", "ts_ns": 2000},
    ]
    res = run_backtest(ticks, ["SPY"], fast=9, slow=21, min_pct=0.15,
                       stop_loss_pct=1.0, take_profit_pct=2.0)

    assert res["total_trades"] == 1
    assert len(res["closed_pnls"]) == 1
    assert res["closed_pnls"][0] > 0  # the winner liquidated at the final mark
    assert res["win_rate"] == 1.0
    # equity and trade stats now tell the same story (fees are tiny, not zero)
    assert res["final_equity"] == pytest.approx(100_000.0 + res["closed_pnls"][0], abs=1.0)


def test_calibration_overtrading_penalty(monkeypatch) -> None:
    # We mock run_backtest to return specific performance metrics based on the parameters passed
    def mock_run_backtest(ticks, symbols, fast, slow, min_pct, stop_loss_pct, take_profit_pct):
        if fast == 5:
            # Config A: High trading count (100 trades), higher unpenalized score
            # score = 0.10 * 10.0 + 1.0 - 100 * 0.02 = 1.0 + 1.0 - 2.0 = 0.0
            return {
                "final_equity": 110_000.0,
                "total_return": 0.10,
                "total_trades": 100,
                "win_rate": 0.6,
                "profit_factor": 1.5,
                "sharpe": 1.0,
                "closed_pnls": []
            }
        elif fast == 9:
            # Config B: Low trading count (10 trades), lower unpenalized score but higher
            # penalized score
            # score = 0.08 * 10.0 + 0.9 - 10 * 0.02 = 0.8 + 0.9 - 0.2 = 1.5
            return {
                "final_equity": 108_000.0,
                "total_return": 0.08,
                "total_trades": 10,
                "win_rate": 0.6,
                "profit_factor": 1.5,
                "sharpe": 0.9,
                "closed_pnls": []
            }
        else:
            # Default low metrics
            return {
                "final_equity": 90_000.0,
                "total_return": -0.10,
                "total_trades": 5,
                "win_rate": 0.4,
                "profit_factor": 0.8,
                "sharpe": -0.5,
                "closed_pnls": []
            }

    monkeypatch.setattr("entropy.bot.calibration.run_backtest", mock_run_backtest)
    
    # We call calibrate_and_test
    res = calibrate_and_test(n_ticks_back=10, n_ticks_forward=10, seed=123)
    
    # The best parameter should be fast=9 (Config B) because of the penalty on fast=5 (Config A)
    # If the penalty wasn't applied, fast=5 would have been chosen.
    assert res["best_params"]["fast"] == 9


# ---------------------------------------------------------------------------
# Walk-forward K-fold calibration
# ---------------------------------------------------------------------------


def test_split_folds_exact_even() -> None:
    # 10 ticks / 4 folds -> 5 segments of 2 ticks each
    assert split_folds(10, 4) == [
        ((0, 2), (2, 4)),
        ((2, 4), (4, 6)),
        ((4, 6), (6, 8)),
        ((6, 8), (8, 10)),
    ]


def test_split_folds_uneven_remainder() -> None:
    # 11 ticks / 4 folds -> 5 segments, first gets the remainder: [3, 2, 2, 2, 2]
    assert split_folds(11, 4) == [
        ((0, 3), (3, 5)),
        ((3, 5), (5, 7)),
        ((5, 7), (7, 9)),
        ((7, 9), (9, 11)),
    ]


def test_split_folds_properties() -> None:
    for n_ticks, n_folds in [(100, 4), (57, 3), (7, 6), (10_001, 5)]:
        folds = split_folds(n_ticks, n_folds)
        assert len(folds) == n_folds
        for (t0, t1), (e0, e1) in folds:
            # train strictly precedes eval; segments are contiguous & non-empty
            assert 0 <= t0 < t1 == e0 < e1 <= n_ticks
            # no index overlap between train and eval of the same fold
            assert set(range(t0, t1)).isdisjoint(range(e0, e1))
        # fold i's eval segment is fold i+1's train segment (sliding window)
        for i in range(n_folds - 1):
            assert folds[i][1] == folds[i + 1][0]
        # segments tile the full stream exactly
        assert folds[0][0][0] == 0
        assert folds[-1][1][1] == n_ticks


def test_split_folds_invalid() -> None:
    with pytest.raises(ValueError):
        split_folds(10, 0)
    with pytest.raises(ValueError):
        split_folds(10, -1)
    with pytest.raises(ValueError):
        split_folds(3, 3)  # needs at least n_folds + 1 ticks
    with pytest.raises(ValueError):
        split_folds(0, 2)


def test_walk_forward_rejects_bad_fold_count() -> None:
    for bad in (1, 0, -1):
        with pytest.raises(ValueError):
            walk_forward(n_folds=bad, n_ticks=100, seed=1, grid=TINY_GRID)


def test_walk_forward_deterministic() -> None:
    grid = dict(TINY_GRID, fast=[5, 9])  # 2 combos so grid search actually selects
    r1 = walk_forward(n_folds=2, n_ticks=600, seed=99, grid=grid)
    r2 = walk_forward(n_folds=2, n_ticks=600, seed=99, grid=grid)
    assert r1 == r2


def test_walk_forward_end_to_end_tiny() -> None:
    res = walk_forward(n_folds=2, n_ticks=400, seed=5, grid=TINY_GRID)

    assert res["n_folds"] == 2
    assert res["n_ticks"] == 400
    assert len(res["symbols"]["equities"]) == 3
    assert len(res["symbols"]["crypto"]) == 3

    assert len(res["folds"]) == 2
    for i, fold in enumerate(res["folds"], start=1):
        assert fold["fold"] == i
        (t0, t1), (e0, e1) = fold["train_range"], fold["eval_range"]
        assert t0 < t1 <= e0 < e1
        for key in ("fast", "slow", "min_pct", "threshold", "bar_s",
                    "stop_loss_pct", "take_profit_pct"):
            assert key in fold["params"]
        assert set(fold["oos"]) == {
            "total_return", "win_rate", "profit_factor", "sharpe", "total_trades",
        }

    agg = res["aggregate"]
    for key in ("mean_return", "median_return", "worst_fold_return",
                "worst_fold", "distinct_param_sets"):
        assert key in agg
    returns = [f["oos"]["total_return"] for f in res["folds"]]
    assert agg["worst_fold_return"] == min(returns)
    assert agg["worst_fold"] == returns.index(min(returns)) + 1
    assert 1 <= agg["distinct_param_sets"] <= 2


def test_walk_forward_leakage_tripwire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record exactly which tick indices each backtest sees: eval indices must
    never appear in that fold's training calls, and all training indices must
    strictly precede the eval indices."""
    from entropy.bot import calibration

    base_ts = 1_700_000_000_000_000_000  # generate_ticks base timestamp
    recorded: list[set[int]] = []

    def spy(
        ticks: list[dict[str, Any]],
        symbols: list[str],
        fast: int,
        slow: int,
        min_pct: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        *,
        threshold: float | None = None,
        bar_s: float | None = None,
    ) -> dict[str, Any]:
        recorded.append({(t["ts_ns"] - base_ts) // 1_000_000_000 for t in ticks})
        return {
            "final_equity": 100_000.0, "total_return": 0.0, "total_trades": 0,
            "win_rate": 0.0, "profit_factor": 1.0, "sharpe": 0.0, "closed_pnls": [],
        }

    monkeypatch.setattr(calibration, "run_backtest", spy)
    res = calibration.walk_forward(n_folds=2, n_ticks=60, seed=7, grid=TINY_GRID)

    # 1-combo grid -> per fold: one train call then one eval call
    assert len(recorded) == 4
    for i, fold in enumerate(res["folds"]):
        train_idx, eval_idx = recorded[2 * i], recorded[2 * i + 1]
        assert train_idx == set(range(*fold["train_range"]))
        assert eval_idx == set(range(*fold["eval_range"]))
        assert not (train_idx & eval_idx)
        assert max(train_idx) < min(eval_idx)


def _stub_wf_result() -> dict[str, Any]:
    params = {"fast": 9, "slow": 21, "min_pct": 0.15, "threshold": 0.5,
              "bar_s": 5.0, "stop_loss_pct": 1.0, "take_profit_pct": 2.0}
    oos = {"total_return": 0.01, "win_rate": 0.5, "profit_factor": 1.2,
           "sharpe": 0.3, "total_trades": 4}
    return {
        "n_folds": 2, "n_ticks": 100, "seed": 7,
        "symbols": {"equities": ["A", "B", "C"], "crypto": ["X", "Y", "Z"]},
        "folds": [
            {"fold": 1, "train_range": (0, 33), "eval_range": (33, 66),
             "params": dict(params), "oos": dict(oos)},
            {"fold": 2, "train_range": (33, 66), "eval_range": (66, 100),
             "params": dict(params), "oos": dict(oos)},
        ],
        "aggregate": {"mean_return": 0.01, "median_return": 0.01,
                      "worst_fold_return": 0.01, "worst_fold": 1,
                      "mean_win_rate": 0.5, "mean_sharpe": 0.3,
                      "total_oos_trades": 8, "distinct_param_sets": 1},
    }


def test_cli_walk_forward_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from entropy.bot import calibration

    called: dict[str, Any] = {}

    def fake_wf(n_folds: int = 4, n_ticks: int = 9_000, seed: int = 42,
                grid: Any = None) -> dict[str, Any]:
        called["n_folds"] = n_folds
        called["seed"] = seed
        return _stub_wf_result()

    monkeypatch.setattr(calibration, "walk_forward", fake_wf)
    from entropy.__main__ import main

    main(["calibrate", "--walk-forward", "2", "--seed", "7"])
    assert called == {"n_folds": 2, "seed": 7}


def test_cli_walk_forward_rejects_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from entropy.__main__ import main
    from entropy.bot import calibration

    def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("walk_forward must not run for invalid N")

    monkeypatch.setattr(calibration, "walk_forward", boom)
    for bad in ("1", "0", "-1"):
        with pytest.raises(SystemExit):
            main(["calibrate", "--walk-forward", bad])


