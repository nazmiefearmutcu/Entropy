"""Unit tests for the rolling win-rate gate (scripts/entropy_wr_gate.py):
the Wilson 95% interval and the PASS/FAIL predicate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load_script(name: str):
    """Import a scripts/ file by path (scripts are not a package)."""
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# The gate imports entropy_accuracy_btc15m at module scope; register it first
# so the gate's `from entropy_accuracy_btc15m import fetch_klines, simulate`
# resolves (its sys.path insertion for src/ is harmless when repeated).
_load_script("entropy_accuracy_btc15m")
mod = _load_script("entropy_wr_gate")


# ---- Wilson interval ----------------------------------------------------------


class TestWilsonInterval:
    def test_matches_the_documented_oos_ci(self):
        """24 trades at 75% -> the CI quoted in PROJECT.md / the design spec
        (~[55.6%, 88.2%] there, to their rounding): the OOS-30d verification
        run's Wilson 95% interval."""
        lo, hi = mod.wilson_interval(18, 24)
        assert lo == pytest.approx(0.551, abs=0.002)
        assert hi == pytest.approx(0.880, abs=0.002)

    def test_matches_the_60d_sample_ci(self):
        """43 trades at 74.4% -> ~[59.8%, 85.1%] (the ledger's ~60d figure)."""
        lo, hi = mod.wilson_interval(32, 43)
        assert lo == pytest.approx(0.598, abs=0.002)
        assert hi == pytest.approx(0.851, abs=0.002)

    def test_degenerates_to_point_estimate_at_large_n(self):
        """z/sqrt(p(1-p)/n) shrinks: 10k flips of a fair coin -> tight [~0.49,
        ~0.51] around 0.5, and the interval is centered near p."""
        lo, hi = mod.wilson_interval(5000, 10_000)
        assert lo == pytest.approx(0.490, abs=0.001)
        assert hi == pytest.approx(0.510, abs=0.001)
        assert (lo + hi) / 2 == pytest.approx(0.5, abs=1e-6)

    def test_wide_interval_on_few_trades(self):
        """1 win in 1 trade: lo sits far below 1 while the point estimate is
        100% — exactly why the gate quotes the CI (75% WR on 24 trades is NOT
        75% +/- epsilon)."""
        lo, hi = mod.wilson_interval(1, 1)
        assert lo == pytest.approx(0.2065, abs=0.005)
        assert hi == 1.0  # Wilson clamps to [0, 1]

    def test_zero_wins_lower_bound_is_zero(self):
        lo, hi = mod.wilson_interval(0, 20)
        assert lo == 0.0
        assert 0.0 < hi < 0.20

    def test_all_wins_upper_bound_is_one(self):
        lo, hi = mod.wilson_interval(20, 20)
        assert hi == 1.0
        assert lo > 0.80

    def test_zero_trades_spans_everything(self):
        assert mod.wilson_interval(0, 0) == (0.0, 1.0)

    def test_is_symmetric_in_the_mirror_case(self):
        """Wilson is symmetric: k wins in n <-> n-k wins in n mirror around 0.5."""
        lo_a, hi_a = mod.wilson_interval(7, 20)
        lo_b, hi_b = mod.wilson_interval(13, 20)
        assert lo_a == pytest.approx(1 - hi_b, abs=1e-9)
        assert hi_a == pytest.approx(1 - lo_b, abs=1e-9)


# ---- gate predicate -----------------------------------------------------------


class TestGateVerdict:
    def test_the_documented_h_row_passes(self):
        """OOS 30d verification run: WR 75.0%, 24t, PF 1.45, +1.44%."""
        passed, failed = mod.gate_verdict(0.75, 24, 1.45, 1.44)
        assert passed and failed == []

    def test_win_rate_at_exactly_60_fails(self):
        """The directive is STRICTLY above 60%: 0.60 is a FAIL."""
        passed, failed = mod.gate_verdict(0.60, 24, 1.45, 1.44)
        assert not passed
        assert any("win_rate" in r for r in failed)

    def test_just_below_60_fails(self):
        passed, failed = mod.gate_verdict(0.581, 31, 0.93, 0.10)
        assert not passed
        assert any("win_rate" in r for r in failed)

    def test_hollow_win_rate_low_pf_fails(self):
        """WR > 60 but PF < 1 (no-hollow-WR guard): FAIL."""
        passed, failed = mod.gate_verdict(0.65, 25, 0.8, 1.0)
        assert not passed
        assert any("profit_factor" in r for r in failed)

    def test_too_few_trades_fails(self):
        """7d tail shape: WR 71.4% but only 7 trades — not deployable evidence."""
        passed, failed = mod.gate_verdict(0.714, 7, 0.56, -0.11)
        assert not passed
        assert any("trades" in r for r in failed)
        assert any("total_return_pct" in r for r in failed)

    def test_negative_return_fails_even_with_good_wr(self):
        passed, failed = mod.gate_verdict(0.70, 30, 1.2, -0.01)
        assert not passed
        assert any("total_return_pct" in r for r in failed)

    def test_zero_return_passes(self):
        """return >= 0: exactly 0.0 is acceptable."""
        passed, failed = mod.gate_verdict(0.62, 22, 1.05, 0.0)
        assert passed and failed == []

    def test_all_four_failures_reported_together(self):
        passed, failed = mod.gate_verdict(0.5, 5, 0.5, -1.0)
        assert not passed
        assert len(failed) == 4

    def test_pf_exactly_1_passes(self):
        """PF >= 1.0: break-even profit factor is acceptable."""
        passed, failed = mod.gate_verdict(0.62, 22, 1.0, 0.5)
        assert passed and failed == []


# ---- config / wins plumbing ----------------------------------------------------


def test_ship_default_cfg_matches_the_accuracy_script_defaults():
    """The gate's config must be 1:1 with the accuracy script's CLI defaults
    (the shipped Round-2 winner s20): same consensus knobs, same risk
    overrides, same 26 bps crypto-spot cost schedule."""
    acc = sys.modules["entropy_accuracy_btc15m"]
    from pathlib import Path as _P

    gate_cfg = mod.build_ship_default_cfg(100.0, acc.SYMBOL, _P("/tmp/wr_gate_test"))
    arg_names = dict(
        bars=2880, warmup_bars=100, cash=100.0,
        threshold=0.5, exit_mode="trail", min_hold_bars=5, cooldown_bars=4,
        cost_edge_mult=1.0, move_floor=0.0003, vote_mode="trend",
        normalize="total", min_participation=0.5, direction_bars=20,
        confirm_bars=2, trail_pct=0.3, long_only=True, max_hold_bars=192,
        stop_mode="sigma", stop_sigma_mult=20.0, tp_sigma_mult=4.0,
    )
    # Build the accuracy script's config from those same values (its main()
    # defaults, pinned here) and compare the knob-bearing subtrees.
    acc_cfg = acc.BotConfig(
        mode="paper", starting_cash=100.0, strategies=("consensus",),
        symbols=(acc.SYMBOL,), ema_symbol=acc.SYMBOL,
        ema_fast=9, ema_slow=21, momentum_min_pct=0.15,
        timeframe="15m", bar_s=900.0, warmup=False,
        market_costs=acc.MarketCostConfig(), cost_aware=True,
        cost_edge_mult=1.0,
        consensus=acc.ConsensusConfig(
            threshold=0.5, exit_mode="trail", min_hold_bars=5,
            cooldown_bars=4, move_floor=0.0003, vote_mode="trend",
            normalize="total", min_participation=0.5, direction_bars=20,
            confirm_bars=2, trail_pct=0.3, max_hold_bars=192, long_only=True,
        ),
        risk_overrides=acc.RiskOverrides(
            per_trade_pct=10.0, max_concurrent=4, stop_loss_pct=1.5,
            take_profit_pct=1.2, max_total_exposure_pct=40.0,
            max_daily_loss_pct=40.0, cooldown_s=180.0,
            min_volatility_pct=0.05, vol_window_s=900.0,
            stop_mode="sigma", stop_sigma_mult=20.0, tp_sigma_mult=4.0,
        ),
        console_log_path="/tmp/wr_gate_test/console.log",
        trade_csv_path="/tmp/wr_gate_test/trades.csv",
    )
    assert gate_cfg.consensus == acc_cfg.consensus
    assert gate_cfg.risk_overrides == acc_cfg.risk_overrides
    assert gate_cfg.market_costs == acc_cfg.market_costs
    assert gate_cfg.cost_edge_mult == acc_cfg.cost_edge_mult
    assert gate_cfg.starting_cash == acc_cfg.starting_cash
    assert gate_cfg.symbols == acc_cfg.symbols
    # the pinned dict stays honest vs the gate's own builder
    assert arg_names["direction_bars"] == gate_cfg.consensus.direction_bars
    assert arg_names["max_hold_bars"] == gate_cfg.consensus.max_hold_bars
    assert arg_names["long_only"] == gate_cfg.consensus.long_only
    assert arg_names["stop_mode"] == gate_cfg.risk_overrides.stop_mode


def test_wins_of_counts_strictly_positive_pnl():
    assert mod.wins_of({"trades": [
        {"pnl": 1.0}, {"pnl": -0.5}, {"pnl": 0.0}, {"pnl": 0.01},
    ]}) == 2


# ---- Round-2 levers (T9a gate parity) ----------------------------------------


def _fake_klines(n: int = 4) -> list[list]:
    """Minimal klines: main() only touches k[0]/k[6] timestamps (end_utc)."""
    base_ms = 1_786_000_000_000
    return [
        [base_ms + i * 900_000, "100", "101", "99", "100.5", "10",
         base_ms + i * 900_000 + 899_999]
        for i in range(n)
    ]


def _fake_passing_report() -> dict:
    return {
        "metrics": {
            "win_rate": 0.75, "total_trades": 24,
            "profit_factor": 1.45, "total_return_pct": 1.44,
        },
        "trades": [{"pnl": 1.0}] * 18 + [{"pnl": -1.0}] * 6,
        "exit_breakdown": {"take_profit": 18, "stop": 6},
    }


def _run_main(monkeypatch, tmp_path, *extra_argv: str):
    """Run the gate's main() with fetch/simulate stubbed (no network)."""
    import json as _json

    captured: dict = {}

    def _fake_fetch(*args, **kwargs):
        captured["fetch_args"] = args
        return _fake_klines()

    def _fake_simulate(klines, cfg, **kwargs):
        captured["cfg"] = cfg
        captured.update(kwargs)
        return _fake_passing_report()

    monkeypatch.setattr(mod, "fetch_klines", _fake_fetch)
    monkeypatch.setattr(mod, "simulate", _fake_simulate)
    monkeypatch.setattr(
        sys, "argv",
        ["entropy_wr_gate.py", "--out", str(tmp_path),
         "--end-date", "2026-09-03T00:00:00Z", *extra_argv],
    )
    rc = mod.main()
    report = _json.loads((tmp_path / "report.json").read_text())
    return rc, captured, report


class TestRound2Levers:
    def test_builder_forwards_vote_mode_and_risk_trail(self):
        """Non-default levers land in the cfg subtrees they belong to."""
        from pathlib import Path as _P

        cfg = mod.build_ship_default_cfg(
            100.0, "binance-spot:BTCUSDT", _P("/tmp/wr_gate_t9a"),
            vote_mode="trend", risk_trail_pct=0.5)
        assert cfg.consensus.vote_mode == "trend"
        assert cfg.risk_overrides.risk_trail_pct == 0.5

    def test_builder_defaults_match_harness_cli_defaults(self):
        """1:1 contract: with defaults, the gate cfg is field-identical to
        the accuracy harness's CLI-default cfg — the shipped Round-2 winner
        s20 (harness: --vote-mode trend, --risk-trail-pct 0.0,
        --stop-sigma-mult 20.0, --tp-sigma-mult 4.0, --max-hold-bars 192,
        --direction-bars 20, --entry-grace-bars 3)."""
        import inspect
        from pathlib import Path as _P

        acc = sys.modules["entropy_accuracy_btc15m"]
        gate_cfg = mod.build_ship_default_cfg(
            100.0, acc.SYMBOL, _P("/tmp/wr_gate_t9a"))
        # The harness defaults, pinned here next to the assertion that uses
        # them (same style as the pre-T9a parity test above).
        assert gate_cfg.consensus.vote_mode == "trend"
        assert gate_cfg.consensus.max_hold_bars == 192
        assert gate_cfg.consensus.direction_bars == 20
        assert gate_cfg.risk_overrides.risk_trail_pct == 0.0
        assert gate_cfg.risk_overrides == acc.RiskOverrides(
            per_trade_pct=10.0, max_concurrent=4, stop_loss_pct=1.5,
            take_profit_pct=1.2, max_total_exposure_pct=40.0,
            max_daily_loss_pct=40.0, cooldown_s=180.0,
            min_volatility_pct=0.05, vol_window_s=900.0,
            stop_mode="sigma", stop_sigma_mult=20.0, tp_sigma_mult=4.0,
            risk_trail_pct=0.0,
        )
        # entry_grace_bars lives on simulate(), not on cfg: the gate's CLI
        # default must equal the harness CLI default (3 = shipped), while the
        # simulate() signature default stays 0 (the T6 byte-identical
        # guarantee for explicit grace=0 runs).
        assert inspect.signature(acc.simulate).parameters[
            "entry_grace_bars"].default == 0
        params = inspect.signature(mod.build_ship_default_cfg).parameters
        assert params["vote_mode"].default == "trend"
        assert params["risk_trail_pct"].default == 0.0
        assert params["stop_sigma_mult"].default == 20.0
        assert params["tp_sigma_mult"].default == 4.0
        assert params["max_hold_bars"].default == 192
        assert params["direction_bars"].default == 20

    def test_gate_cli_defaults_match_harness_cli_defaults(
            self, tmp_path, monkeypatch):
        """End-to-end 1:1: run BOTH mains with fetch/simulate stubbed and no
        knob flags — the gate's cfg subtrees must equal the harness's, and
        both must forward the shipped entry_grace_bars=3 to simulate()."""
        acc = sys.modules["entropy_accuracy_btc15m"]

        gate_captured: dict = {}
        harness_captured: dict = {}

        def _fake_fetch(*args, **kwargs):
            return _fake_klines()

        def _fake_gate_simulate(klines, cfg, **kwargs):
            gate_captured["cfg"] = cfg
            gate_captured.update(kwargs)
            return _fake_passing_report()

        def _fake_harness_simulate(klines, cfg, **kwargs):
            harness_captured["cfg"] = cfg
            harness_captured.update(kwargs)
            return {
                "metrics": {
                    "final_equity": 100.0, "total_return_pct": 0.0,
                    "total_trades": 0, "win_rate": 0.0,
                    "win_definition": "pnl > 0",
                    "win_rate_long_only": 0.0,
                    "total_trades_long_only": 0,
                    "profit_factor": 1.0, "sharpe": 0.0,
                    "costs_paid": 0.0, "max_drawdown_pct": 0.0,
                    "max_exposure_pct_observed": 0.0,
                    "max_concurrent_open": 0,
                    "total_notional_traded": 0.0,
                    "notional_turnover_x": 0.0, "halted": False,
                    "best_day_pct": 0.0, "worst_day_pct": 0.0,
                },
                "daily": {},
                "trades": [],
                "exit_breakdown": {},
                "avg_hold_bars": 0.0,
                "rejects": {},
                "warmup": {"bars": 100, "trades": 0},
                "entry_grace_bars": 0,
            }

        monkeypatch.setattr(mod, "fetch_klines", _fake_fetch)
        monkeypatch.setattr(mod, "simulate", _fake_gate_simulate)
        monkeypatch.setattr(
            sys, "argv",
            ["entropy_wr_gate.py", "--out", str(tmp_path),
             "--end-date", "2026-09-03T00:00:00Z"],
        )
        assert mod.main() in (0, 1)
        monkeypatch.setattr(acc, "fetch_klines", _fake_fetch)
        monkeypatch.setattr(acc, "simulate", _fake_harness_simulate)
        monkeypatch.setattr(
            sys, "argv", ["entropy_accuracy_btc15m.py", "--out", str(tmp_path)],
        )
        acc.main()
        # default symbol is BTCUSDT on both CLIs
        assert gate_captured["cfg"].consensus == harness_captured["cfg"].consensus
        assert (gate_captured["cfg"].risk_overrides
                == harness_captured["cfg"].risk_overrides)
        assert gate_captured["entry_grace_bars"] == 3
        assert harness_captured["entry_grace_bars"] == 3

    def test_main_forwards_all_knobs_to_builder_and_simulate(
            self, tmp_path, monkeypatch, capsys):
        """Every ship-default knob flag reaches the builder and simulate();
        report.json + console echo carry all of them."""
        rc, captured, report = _run_main(
            monkeypatch, tmp_path,
            "--vote-mode", "adaptive",
            "--risk-trail-pct", "0.5",
            "--entry-grace-bars", "2",
            "--stop-sigma-mult", "8.0",
            "--tp-sigma-mult", "6.0",
            "--max-hold-bars", "96",
            "--direction-bars", "0")
        assert rc == 0
        assert captured["entry_grace_bars"] == 2
        assert captured["cfg"].consensus.vote_mode == "adaptive"
        assert captured["cfg"].consensus.max_hold_bars == 96
        assert captured["cfg"].consensus.direction_bars == 0
        assert captured["cfg"].risk_overrides.risk_trail_pct == 0.5
        assert captured["cfg"].risk_overrides.stop_sigma_mult == 8.0
        assert captured["cfg"].risk_overrides.tp_sigma_mult == 6.0
        assert report["config"]["vote_mode"] == "adaptive"
        assert report["config"]["risk_trail_pct"] == 0.5
        assert report["config"]["entry_grace_bars"] == 2
        assert report["config"]["stop_sigma_mult"] == 8.0
        assert report["config"]["tp_sigma_mult"] == 6.0
        assert report["config"]["max_hold_bars"] == 96
        assert report["config"]["direction_bars"] == 0
        out = capsys.readouterr().out
        assert "vote_mode=adaptive" in out
        assert "risk_trail_pct=0.5" in out
        assert "entry_grace_bars=2" in out
        assert "stop 8x / tp 6x sigma" in out
        assert "max_hold_bars=96" in out
        assert "direction_bars=0" in out

    def test_main_defaults_match_shipped_s20(self, tmp_path, monkeypatch,
                                             capsys):
        """No knob flags: simulate() gets entry_grace_bars=3, cfg carries the
        shipped s20 values, and the echo shows the defaults."""
        rc, captured, report = _run_main(monkeypatch, tmp_path)
        assert rc == 0
        assert captured["entry_grace_bars"] == 3
        assert captured["cfg"].consensus.vote_mode == "trend"
        assert captured["cfg"].consensus.max_hold_bars == 192
        assert captured["cfg"].consensus.direction_bars == 20
        assert captured["cfg"].risk_overrides.stop_sigma_mult == 20.0
        assert captured["cfg"].risk_overrides.tp_sigma_mult == 4.0
        assert captured["cfg"].risk_overrides.risk_trail_pct == 0.0
        assert report["config"]["vote_mode"] == "trend"
        assert report["config"]["risk_trail_pct"] == 0.0
        assert report["config"]["entry_grace_bars"] == 3
        assert report["config"]["stop_sigma_mult"] == 20.0
        assert report["config"]["max_hold_bars"] == 192
        out = capsys.readouterr().out
        assert "vote_mode=trend" in out
        assert "entry_grace_bars=3" in out

    def test_invalid_vote_mode_rejected(self, monkeypatch, tmp_path):
        """--vote-mode is restricted to the four consensus modes."""
        monkeypatch.setattr(
            sys, "argv",
            ["entropy_wr_gate.py", "--out", str(tmp_path),
             "--vote-mode", "momentum"])
        with pytest.raises(SystemExit):
            mod.main()
