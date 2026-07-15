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
