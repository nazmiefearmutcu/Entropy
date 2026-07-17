import builtins
import csv
import json
import logging
import os
from pathlib import Path

import pytest

import entropy.bot.ledger as ledger_mod
from entropy.bot.ledger import (
    Ledger,
    init_trade_csv,
    record_trade_close,
    record_trade_open,
)
from entropy.bot.orders import Fill, OrderIntent, OrderSide
from entropy.bot.portfolio import Portfolio, PositionSide


def test_record_fill_writes_csv_and_jsonl(tmp_path: Path):
    led = Ledger(str(tmp_path))
    f = Fill(order_id="o1", symbol="SPY", side=OrderSide.BUY, qty=10.0,
             price=100.0, fee=0.1, slippage=0.05, ts_ns=1)
    led.record_fill(f, OrderIntent.OPEN)
    rows = list(csv.DictReader((tmp_path / "fills.csv").open()))
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["intent"] == "open"
    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert json.loads(lines[0])["kind"] == "fill"


def test_record_equity_appends_row(tmp_path: Path):
    led = Ledger(str(tmp_path))
    p = Portfolio(1000.0)
    p.open("A", PositionSide.LONG, 1.0, 10.0, 9.0, 11.0, 1, 0.0)
    p.mark("A", 12.0)
    led.record_equity(p.snapshot(ts_ns=5))
    rows = list(csv.DictReader((tmp_path / "equity.csv").open()))
    assert float(rows[0]["equity"]) == 1002.0


def test_record_risk_change_and_reject(tmp_path: Path):
    led = Ledger(str(tmp_path))
    led.record_risk_change("Medium", "Extreme")
    led.record_reject("SPY", "cooldown active")
    kinds = [json.loads(x)["kind"]
             for x in (tmp_path / "events.jsonl").read_text().strip().splitlines()]
    assert "risk_profile_changed" in kinds
    assert "reject" in kinds


def _rows_without_ts(trade_csv: Path) -> list[list[str]]:
    """Rows with the ts column stripped (it is wall-clock and not assertable exactly)."""
    rows = list(csv.reader(trade_csv.open(encoding="utf-8")))
    for row in rows[1:]:
        float(row[0])  # ts must still parse as epoch seconds
    return [rows[0]] + [row[1:] for row in rows[1:]]


def test_trade_csv_logging_open_close(tmp_path: Path):
    trade_csv = tmp_path / "trades.csv"

    # Initialize Ledger with specific trade_csv_path
    led = Ledger(str(tmp_path), mode="paper", trade_csv_path=str(trade_csv))

    # Check that it's initialized with the append-only header
    assert trade_csv.exists()
    rows = _rows_without_ts(trade_csv)
    assert rows[0] == ["ts", "symbol", "side", "event", "open_price", "close_price"]

    # Record open LONG on AAPL
    led.record_trade_open("AAPL", "LONG", 150.0)
    rows = _rows_without_ts(trade_csv)
    assert len(rows) == 2
    assert rows[1] == ["AAPL", "LONG", "OPEN", "150.0", ""]

    # Record open SHORT on BTCUSDT
    led.record_trade_open("BTCUSDT", "SHORT", 50000.0)

    # Record close LONG on AAPL: appended CLOSE row carries entry AND exit price
    led.record_trade_close("AAPL", "LONG", 155.0)
    rows = _rows_without_ts(trade_csv)
    assert len(rows) == 4
    assert rows[1] == ["AAPL", "LONG", "OPEN", "150.0", ""]
    assert rows[2] == ["BTCUSDT", "SHORT", "OPEN", "50000.0", ""]
    assert rows[3] == ["AAPL", "LONG", "CLOSE", "150.0", "155.0"]

    # Record close SHORT on BTCUSDT
    led.record_trade_close("BTCUSDT", "SHORT", 49000.0)
    rows = _rows_without_ts(trade_csv)
    assert len(rows) == 5
    assert rows[4] == ["BTCUSDT", "SHORT", "CLOSE", "50000.0", "49000.0"]


def test_trade_csv_logging_case_insensitive(tmp_path: Path):
    trade_csv = tmp_path / "trades_case.csv"
    led = Ledger(str(tmp_path), mode="paper", trade_csv_path=str(trade_csv))

    # Record open with uppercase, close with mixed case/lowercase:
    # matching is case-insensitive, rows keep the caller's casing.
    led.record_trade_open("AAPL", "LONG", 150.0)
    led.record_trade_close("aapl", "long", 155.0)

    rows = _rows_without_ts(trade_csv)
    assert len(rows) == 3
    assert rows[1] == ["AAPL", "LONG", "OPEN", "150.0", ""]
    assert rows[2] == ["aapl", "long", "CLOSE", "150.0", "155.0"]

    # Record open with lowercase, close with uppercase
    led.record_trade_open("btcUSDT", "short", 50000.0)
    led.record_trade_close("BTCUSDT", "SHORT", 49000.0)

    rows = _rows_without_ts(trade_csv)
    assert len(rows) == 5
    assert rows[3] == ["btcUSDT", "short", "OPEN", "50000.0", ""]
    assert rows[4] == ["BTCUSDT", "SHORT", "CLOSE", "50000.0", "49000.0"]


# ---------------------------------------------------------------------------
# Append-only trade CSV: proving tests (verify-first for the O(n) rewrite and
# the silent `except Exception: pass` in record_trade_close).
# ---------------------------------------------------------------------------


def test_trade_close_is_pure_append(tmp_path: Path):
    """A close must append one row; bytes already on disk must never change."""
    trade_csv = tmp_path / "trades.csv"
    record_trade_open(str(trade_csv), "AAPL", "LONG", 150.0)
    record_trade_open(str(trade_csv), "MSFT", "LONG", 300.0)
    record_trade_open(str(trade_csv), "GOOGL", "SHORT", 2800.0)
    before = trade_csv.read_bytes()

    record_trade_close(str(trade_csv), "MSFT", "LONG", 310.0)

    after = trade_csv.read_bytes()
    assert after.startswith(before), "close rewrote existing rows instead of appending"
    assert len(after) > len(before)
    last = list(csv.reader(after.decode("utf-8").splitlines()))[-1]
    assert last[1:] == ["MSFT", "LONG", "CLOSE", "300.0", "310.0"]


def test_trade_close_oserror_logged_once_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """An OSError while appending must not raise, and must be logged (DEBUG) exactly once."""
    trade_csv = tmp_path / "trades.csv"
    record_trade_open(str(trade_csv), "AAPL", "LONG", 150.0)

    monkeypatch.setattr(ledger_mod, "_write_failure_logged", False)
    real_open = builtins.open

    def failing_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if os.fspath(file) == str(trade_csv):
            raise OSError("disk full")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    with caplog.at_level(logging.DEBUG, logger="entropy.bot.ledger"):
        record_trade_close(str(trade_csv), "AAPL", "LONG", 155.0)  # must not raise
        record_trade_close(str(trade_csv), "AAPL", "LONG", 156.0)  # second failure: silent
    failures = [r for r in caplog.records if "trade csv" in r.getMessage().lower()]
    assert len(failures) == 1, "write failure must be logged exactly once"
    assert all(r.levelno == logging.DEBUG for r in failures)


def test_fresh_file_clears_stale_registry(tmp_path: Path):
    """The module-scope open-position registry is keyed by path and used to
    survive file recreation: a stale entry would pair a future CLOSE with an
    entry price whose OPEN row is not in the fresh file."""
    trade_csv = tmp_path / "trades.csv"
    record_trade_open(str(trade_csv), "AAPL", "LONG", 150.0)

    # The file starts over (deleted / replaced between runs); registry state from
    # the previous file must go with it.
    trade_csv.unlink()
    init_trade_csv(str(trade_csv))

    record_trade_close(str(trade_csv), "AAPL", "LONG", 155.0)
    rows = _rows_without_ts(trade_csv)
    assert len(rows) == 2
    assert rows[1] == ["AAPL", "LONG", "CLOSE", "", "155.0"]  # no foreign pairing


def test_legacy_rename_clears_stale_registry(tmp_path: Path):
    """Same guarantee when the fresh file is created by the legacy-header rename."""
    trade_csv = tmp_path / "trades.csv"
    record_trade_open(str(trade_csv), "AAPL", "LONG", 150.0)

    trade_csv.write_text(
        "Symbol,Side,Open Price,Close Price\r\nAAPL,LONG,150.0,\r\n", encoding="utf-8"
    )
    init_trade_csv(str(trade_csv))  # renames to .bak, starts a fresh file

    record_trade_close(str(trade_csv), "AAPL", "LONG", 155.0)
    rows = _rows_without_ts(trade_csv)
    assert rows[-1] == ["AAPL", "LONG", "CLOSE", "", "155.0"]


def test_trade_close_oserror_keeps_registry_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The entry price is popped only after the CLOSE row lands on disk: a failed
    write must leave the stack intact so a retried close still pairs correctly."""
    trade_csv = tmp_path / "trades.csv"
    record_trade_open(str(trade_csv), "AAPL", "LONG", 150.0)

    real_open = builtins.open
    failing = {"on": True}

    def flaky_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if failing["on"] and os.fspath(file) == str(trade_csv) and "a" in mode:
            raise OSError("disk full")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", flaky_open)
    record_trade_close(str(trade_csv), "AAPL", "LONG", 155.0)  # append fails
    stack = ledger_mod._open_positions[str(trade_csv)][("AAPL", "LONG")]
    assert stack == [150.0]  # entry NOT lost with the failed row

    failing["on"] = False
    record_trade_close(str(trade_csv), "AAPL", "LONG", 156.0)  # retry succeeds
    rows = _rows_without_ts(trade_csv)
    assert rows[-1] == ["AAPL", "LONG", "CLOSE", "150.0", "156.0"]
    assert ledger_mod._open_positions[str(trade_csv)][("AAPL", "LONG")] == []


def test_legacy_trade_csv_renamed_to_bak(tmp_path: Path):
    """A file with the old rewrite-format header is set aside, never mixed with new rows."""
    trade_csv = tmp_path / "trades.csv"
    trade_csv.write_text(
        "Symbol,Side,Open Price,Close Price\r\nAAPL,LONG,150.0,\r\n", encoding="utf-8"
    )

    init_trade_csv(str(trade_csv))

    baks = list(tmp_path.glob("trades.csv.bak-*"))
    assert len(baks) == 1
    assert "Symbol,Side,Open Price,Close Price" in baks[0].read_text(encoding="utf-8")
    rows = list(csv.reader(trade_csv.open(encoding="utf-8")))
    assert rows == [["ts", "symbol", "side", "event", "open_price", "close_price"]]
