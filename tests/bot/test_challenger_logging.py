import csv
from pathlib import Path

from entropy.bot.ledger import init_trade_csv, record_trade_close, record_trade_open


def read_csv_rows(csv_path: Path) -> list[list[str]]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))

def test_consecutive_trades_same_symbol(tmp_path: Path):
    """Verify consecutive trade opens and closes for the same symbol."""
    csv_path = tmp_path / "consecutive_trades.csv"
    
    # Trade 1: Open LONG on AAPL
    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)
    # Trade 1: Close LONG on AAPL
    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)
    
    # Trade 2: Open SHORT on AAPL
    record_trade_open(str(csv_path), "AAPL", "SHORT", 160.0)
    # Trade 2: Close SHORT on AAPL
    record_trade_close(str(csv_path), "AAPL", "SHORT", 158.0)
    
    rows = read_csv_rows(csv_path)
    assert len(rows) == 3, f"Expected 3 rows (header + 2 trades), got {len(rows)}"
    assert rows[0] == ["Symbol", "Side", "Open Price", "Close Price"]
    assert rows[1] == ["AAPL", "LONG", "150.0", "155.0"]
    assert rows[2] == ["AAPL", "SHORT", "160.0", "158.0"]

def test_multiple_active_trades_different_symbols(tmp_path: Path):
    """Verify multiple active trades for different symbols."""
    csv_path = tmp_path / "multiple_active_trades.csv"
    
    # Open positions for multiple symbols
    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)
    record_trade_open(str(csv_path), "MSFT", "LONG", 300.0)
    record_trade_open(str(csv_path), "GOOGL", "SHORT", 2800.0)
    
    # Close them in interleaved order
    record_trade_close(str(csv_path), "MSFT", "LONG", 310.0)
    record_trade_close(str(csv_path), "AAPL", "LONG", 148.0)
    record_trade_close(str(csv_path), "GOOGL", "SHORT", 2750.0)
    
    rows = read_csv_rows(csv_path)
    assert len(rows) == 4
    # The order of rows in CSV should match open order
    assert rows[1] == ["AAPL", "LONG", "150.0", "148.0"]
    assert rows[2] == ["MSFT", "LONG", "300.0", "310.0"]
    assert rows[3] == ["GOOGL", "SHORT", "2800.0", "2750.0"]

def test_backward_search_multiple_open_lifo(tmp_path: Path):
    """Verify LIFO matching for multiple open positions of the same symbol and side."""
    csv_path = tmp_path / "backward_search_lifo.csv"
    
    # Open AAPL twice
    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0) # Trade A
    record_trade_open(str(csv_path), "AAPL", "LONG", 160.0) # Trade B
    
    # Close AAPL once. Since it's backward search, it should match Trade B (160.0)
    record_trade_close(str(csv_path), "AAPL", "LONG", 165.0)
    
    rows = read_csv_rows(csv_path)
    assert rows[1] == ["AAPL", "LONG", "150.0", ""]
    assert rows[2] == ["AAPL", "LONG", "160.0", "165.0"]
    
    # Close AAPL again. It should now match Trade A (150.0)
    record_trade_close(str(csv_path), "AAPL", "LONG", 152.0)
    
    rows = read_csv_rows(csv_path)
    assert rows[1] == ["AAPL", "LONG", "150.0", "152.0"]
    assert rows[2] == ["AAPL", "LONG", "160.0", "165.0"]

def test_backward_search_side_matching_bug(tmp_path: Path):
    """Test whether record_trade_close correctly uses the 'side' parameter to match trades.
    If it ignores 'side', closing a LONG trade might incorrectly match an open SHORT trade."""
    csv_path = tmp_path / "side_matching.csv"
    
    # Open LONG then SHORT on the same symbol
    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)
    record_trade_open(str(csv_path), "AAPL", "SHORT", 160.0)
    
    # Close the LONG position first (at 155.0)
    # If the side is ignored, the backward search will look at the last row (SHORT at 160.0),
    # see that AAPL matches, and update the SHORT's close price to 155.0.
    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)
    
    rows = read_csv_rows(csv_path)
    
    # Let's inspect the rows.
    # Expected correct behaviour: The LONG trade (first row) is closed at 155.0, and SHORT
    # remains open.
    # Actual current behavior: The SHORT trade (second row) is closed at 155.0.
    print(f"\nRow 1: {rows[1]}")
    print(f"Row 2: {rows[2]}")
    
    # We assert the correct/expected behavior (this might fail if the implementation is buggy!)
    # Let's write the assertion that expects the correct behavior to verify if it fails.
    assert rows[1] == ["AAPL", "LONG", "150.0", "155.0"], "LONG trade should be closed at 155.0"
    assert rows[2] == ["AAPL", "SHORT", "160.0", ""], "SHORT trade should still be open"

def test_close_non_existent_trade(tmp_path: Path):
    """Verify behavior when trying to close a trade that was never opened."""
    csv_path = tmp_path / "non_existent.csv"
    
    # Initialize header
    init_trade_csv(str(csv_path))
    initial_rows = read_csv_rows(csv_path)
    
    # Try closing AAPL LONG
    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)
    
    # Rows should remain unchanged
    rows = read_csv_rows(csv_path)
    assert rows == initial_rows
    assert len(rows) == 1


def test_concurrency_opposite_sides_interleaved(tmp_path: Path):
    """Stress test with opposite sides and interleaved open/close orders."""
    csv_path = tmp_path / "opposite_sides_interleaved.csv"
    
    # 1. Open LONG AAPL (price 150)
    record_trade_open(str(csv_path), "AAPL", "LONG", 150.0)
    # 2. Open SHORT AAPL (price 160)
    record_trade_open(str(csv_path), "AAPL", "SHORT", 160.0)
    # 3. Open LONG AAPL (price 152) -> second LONG position
    record_trade_open(str(csv_path), "AAPL", "LONG", 152.0)
    
    # Check intermediate state
    rows = read_csv_rows(csv_path)
    assert len(rows) == 4
    assert rows[1] == ["AAPL", "LONG", "150.0", ""]
    assert rows[2] == ["AAPL", "SHORT", "160.0", ""]
    assert rows[3] == ["AAPL", "LONG", "152.0", ""]
    
    # 4. Close LONG AAPL (price 155). Should match last LONG (152)
    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)
    rows = read_csv_rows(csv_path)
    assert rows[3] == ["AAPL", "LONG", "152.0", "155.0"] # LIFO matched
    assert rows[1] == ["AAPL", "LONG", "150.0", ""]      # first LONG untouched
    assert rows[2] == ["AAPL", "SHORT", "160.0", ""]     # SHORT untouched
    
    # 5. Close SHORT AAPL (price 158). Should match SHORT (160)
    record_trade_close(str(csv_path), "AAPL", "SHORT", 158.0)
    rows = read_csv_rows(csv_path)
    assert rows[2] == ["AAPL", "SHORT", "160.0", "158.0"]
    assert rows[1] == ["AAPL", "LONG", "150.0", ""]
    
    # 6. Close LONG AAPL (price 151). Should match first LONG (150)
    record_trade_close(str(csv_path), "AAPL", "LONG", 151.0)
    rows = read_csv_rows(csv_path)
    assert rows[1] == ["AAPL", "LONG", "150.0", "151.0"]


def test_case_insensitivity_and_whitespace(tmp_path: Path):
    """Verify that matching handles case-insensitivity correctly."""
    csv_path = tmp_path / "case_insensitivity.csv"
    
    record_trade_open(str(csv_path), "AAPL", "long", 150.0)
    record_trade_open(str(csv_path), "AAPL", "Short", 160.0)
    
    # Close using different cases
    record_trade_close(str(csv_path), "AAPL", "LONG", 155.0)
    record_trade_close(str(csv_path), "AAPL", "short", 158.0)
    
    rows = read_csv_rows(csv_path)
    assert rows[1] == ["AAPL", "long", "150.0", "155.0"]
    assert rows[2] == ["AAPL", "Short", "160.0", "158.0"]

