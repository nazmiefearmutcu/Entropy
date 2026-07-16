# tests/test_cli.py
from __future__ import annotations

import contextlib
import io

import pytest

from entropy.__main__ import main


def test_cli_calibrate():
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        main(["calibrate", "--ticks-back", "100", "--ticks-forward", "100", "--seed", "123"])
    output = f.getvalue()
    
    # Verify it runs successfully and outputs the tables
    assert "Calibrated Optimal Parameters" in output
    assert "Accuracy Performance (Backtest vs Forward Test)" in output


def test_cli_benchmark():
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        main(["benchmark"])
    output = f.getvalue()

    # Verify it runs successfully and outputs the Throughput Summary table
    assert "Throughput Summary" in output


@pytest.mark.parametrize("value", ["sim", "live", "auto"])
def test_cli_equity_source_valid_values(monkeypatch, value):
    received = {}

    def fake_run_ui(console_log=None, trade_csv=None, equity_source=None):
        received["equity_source"] = equity_source

    monkeypatch.setattr("entropy.__main__.run_ui", fake_run_ui)
    main(["ui", "--equity-source", value])
    assert received["equity_source"] == value
    # bare `entropy` default path takes the flag too
    main(["--equity-source", value])
    assert received["equity_source"] == value


def test_cli_equity_source_default_is_unset(monkeypatch):
    received = {}

    def fake_run_ui(console_log=None, trade_csv=None, equity_source=None):
        received["equity_source"] = equity_source

    monkeypatch.setattr("entropy.__main__.run_ui", fake_run_ui)
    main([])
    assert received["equity_source"] is None  # AppConfig default ("auto") applies


def test_cli_equity_source_invalid_rejected():
    with pytest.raises(SystemExit):
        main(["ui", "--equity-source", "bogus"])
    with pytest.raises(SystemExit):
        main(["--equity-source", "LIVE"])
