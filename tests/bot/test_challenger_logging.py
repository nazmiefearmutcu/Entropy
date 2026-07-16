import csv
from pathlib import Path

from entropy.bot.ledger import init_trade_csv, record_trade_close, record_trade_open

_HEADER = ["ts", "symbol", "side", "event", "open_price", "close_price"]


def read_rows(csv_path: Path) -> list[list[str]]:
    """Rows with the wall-clock ts column stripped; header kept as row 0."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for row in rows[1:]:
        float(row[0])  # ts must parse as epoch seconds
    return [rows[0]] + [row[1:] for row in rows[1:]]


def test_consecutive_trades_same_symbol(tmp_path: Path):
    """Consecutive open/close pairs append one row each, in event order."""
    csv_path = tmp_path / "consecutive_trades.csv"

    # Trade 1: Open + close LONG on AAPL
    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)
    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)

    # Trade 2: Open + close SHORT on AAPL
    record_trade_open(str(csv_path), "AAPL", "SHORT", 160.0)
    record_trade_close(str(csv_path), "AAPL", "SHORT", 158.0)

    rows = read_rows(csv_path)
    assert len(rows) == 5, f"Expected 5 rows (header + 4 events), got {len(rows)}"
    assert rows[0] == _HEADER
    assert rows[1] == ["AAPL", "LONG", "OPEN", "150.0", ""]
    assert rows[2] == ["AAPL", "LONG", "CLOSE", "150.0", "155.0"]
    assert rows[3] == ["AAPL", "SHORT", "OPEN", "160.0", ""]
    assert rows[4] == ["AAPL", "SHORT", "CLOSE", "160.0", "158.0"]


def test_multiple_active_trades_different_symbols(tmp_path: Path):
    """Interleaved closes pair with the right symbol's entry price."""
    csv_path = tmp_path / "multiple_active_trades.csv"

    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)
    record_trade_open(str(csv_path), "MSFT", "LONG", 300.0)
    record_trade_open(str(csv_path), "GOOGL", "SHORT", 2800.0)

    # Close them in interleaved order
    record_trade_close(str(csv_path), "MSFT", "LONG", 310.0)
    record_trade_close(str(csv_path), "AAPL", "LONG", 148.0)
    record_trade_close(str(csv_path), "GOOGL", "SHORT", 2750.0)

    rows = read_rows(csv_path)
    assert len(rows) == 7
    # Rows are strictly in event order; CLOSE rows carry the matching entry price.
    assert rows[1] == ["AAPL", "LONG", "OPEN", "150.0", ""]
    assert rows[2] == ["MSFT", "LONG", "OPEN", "300.0", ""]
    assert rows[3] == ["GOOGL", "SHORT", "OPEN", "2800.0", ""]
    assert rows[4] == ["MSFT", "LONG", "CLOSE", "300.0", "310.0"]
    assert rows[5] == ["AAPL", "LONG", "CLOSE", "150.0", "148.0"]
    assert rows[6] == ["GOOGL", "SHORT", "CLOSE", "2800.0", "2750.0"]


def test_multiple_open_positions_matched_lifo(tmp_path: Path):
    """Multiple opens of the same symbol+side are matched LIFO (most recent first)."""
    csv_path = tmp_path / "lifo_matching.csv"

    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)  # Trade A
    record_trade_open(str(csv_path), "AAPL", "LONG", 160.0)  # Trade B

    # First close matches Trade B (160.0), the most recent open
    record_trade_close(str(csv_path), "AAPL", "LONG", 165.0)
    rows = read_rows(csv_path)
    assert rows[3] == ["AAPL", "LONG", "CLOSE", "160.0", "165.0"]

    # Second close matches Trade A (150.0)
    record_trade_close(str(csv_path), "AAPL", "LONG", 152.0)
    rows = read_rows(csv_path)
    assert rows[4] == ["AAPL", "LONG", "CLOSE", "150.0", "152.0"]


def test_close_matches_on_side_not_just_symbol(tmp_path: Path):
    """Closing a LONG must never consume an open SHORT of the same symbol."""
    csv_path = tmp_path / "side_matching.csv"

    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)
    record_trade_open(str(csv_path), "AAPL", "SHORT", 160.0)

    # Close the LONG: its CLOSE row must carry the LONG entry (150.0), not the SHORT's
    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)

    rows = read_rows(csv_path)
    assert rows[3] == ["AAPL", "LONG", "CLOSE", "150.0", "155.0"]

    # The SHORT is still open: closing it now pairs with 160.0
    record_trade_close(str(csv_path), "AAPL", "SHORT", 158.0)
    rows = read_rows(csv_path)
    assert rows[4] == ["AAPL", "SHORT", "CLOSE", "160.0", "158.0"]


def test_close_without_matching_open_still_recorded(tmp_path: Path):
    """A close with no known open (e.g. state lost across a restart) is not dropped:
    it appends a CLOSE row with an empty open_price."""
    csv_path = tmp_path / "non_existent.csv"

    init_trade_csv(str(csv_path))
    record_trade_close(str(csv_path), "NVDA", "LONG", 155.0)

    rows = read_rows(csv_path)
    assert len(rows) == 2
    assert rows[1] == ["NVDA", "LONG", "CLOSE", "", "155.0"]


def test_concurrency_opposite_sides_interleaved(tmp_path: Path):
    """Stress test with opposite sides and interleaved open/close orders."""
    csv_path = tmp_path / "opposite_sides_interleaved.csv"

    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)
    record_trade_open(str(csv_path), "AAPL", "SHORT", 160.0)
    record_trade_open(str(csv_path), "AAPL", "LONG", 152.0)

    rows = read_rows(csv_path)
    assert len(rows) == 4
    assert rows[1] == ["AAPL", "LONG", "OPEN", "150.0", ""]
    assert rows[2] == ["AAPL", "SHORT", "OPEN", "160.0", ""]
    assert rows[3] == ["AAPL", "LONG", "OPEN", "152.0", ""]

    # Close LONG at 155: LIFO pairs with the second LONG (152)
    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)
    rows = read_rows(csv_path)
    assert rows[4] == ["AAPL", "LONG", "CLOSE", "152.0", "155.0"]

    # Close SHORT at 158: pairs with the SHORT (160)
    record_trade_close(str(csv_path), "AAPL", "SHORT", 158.0)
    rows = read_rows(csv_path)
    assert rows[5] == ["AAPL", "SHORT", "CLOSE", "160.0", "158.0"]

    # Close LONG at 151: pairs with the first LONG (150)
    record_trade_close(str(csv_path), "AAPL", "LONG", 151.0)
    rows = read_rows(csv_path)
    assert rows[6] == ["AAPL", "LONG", "CLOSE", "150.0", "151.0"]


def test_case_insensitivity(tmp_path: Path):
    """Matching is case-insensitive; rows keep the caller's casing."""
    csv_path = tmp_path / "case_insensitivity.csv"

    record_trade_open(str(csv_path), "AAPL", "long", 150.0)
    record_trade_open(str(csv_path), "AAPL", "Short", 160.0)

    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)
    record_trade_close(str(csv_path), "AAPL", "short", 158.0)

    rows = read_rows(csv_path)
    assert rows[1] == ["AAPL", "long", "OPEN", "150.0", ""]
    assert rows[2] == ["AAPL", "Short", "OPEN", "160.0", ""]
    assert rows[3] == ["AAPL", "LONG", "CLOSE", "150.0", "155.0"]
    assert rows[4] == ["AAPL", "short", "CLOSE", "160.0", "158.0"]
