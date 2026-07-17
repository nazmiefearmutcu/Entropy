# tests/data/test_watchlist.py
"""Watchlist: JSON-file persistence with atomic writes and corruption recovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from entropy.data.universe import SymbolInfo
from entropy.data.watchlist import Watchlist

AAPL = SymbolInfo(symbol="AAPL", name="Apple Inc.", asset_class="equity", venue="us")
MSFT = SymbolInfo(symbol="MSFT", name="Microsoft Corp", asset_class="equity", venue="us")
BTC = SymbolInfo(
    symbol="binance-spot:BTCUSDT",
    name="Bitcoin · Binance spot",
    asset_class="crypto",
    venue="binance-spot",
)


def wl(tmp_path: Path) -> Watchlist:
    return Watchlist(tmp_path / "watchlist.json")


# --- core operations ----------------------------------------------------------

def test_add_remove_contains(tmp_path: Path) -> None:
    w = wl(tmp_path)
    assert "AAPL" not in w
    assert w.add(AAPL) is True
    assert "AAPL" in w
    assert w.add(AAPL) is False          # duplicate add is a no-op
    assert w.remove("AAPL") is True
    assert "AAPL" not in w
    assert w.remove("AAPL") is False     # absent remove is a no-op


def test_toggle(tmp_path: Path) -> None:
    w = wl(tmp_path)
    assert w.toggle(BTC) is True         # now present
    assert "binance-spot:BTCUSDT" in w
    assert w.toggle(BTC) is False        # now absent
    assert "binance-spot:BTCUSDT" not in w


def test_items_insertion_order_and_dedupe(tmp_path: Path) -> None:
    w = wl(tmp_path)
    w.add(MSFT)
    w.add(AAPL)
    w.add(BTC)
    w.add(MSFT)  # duplicate: keeps original position
    assert [i.symbol for i in w.items()] == ["MSFT", "AAPL", "binance-spot:BTCUSDT"]
    assert w.items()[0] == MSFT


# --- persistence --------------------------------------------------------------

def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.json"
    w = Watchlist(path)
    w.add(AAPL)
    w.add(BTC)
    reloaded = Watchlist(path)
    assert reloaded.items() == [AAPL, BTC]  # full SymbolInfo survives, incl. venue


def test_parent_dir_auto_created(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "watchlist.json"
    w = Watchlist(path)
    w.add(AAPL)
    assert path.exists()
    assert Watchlist(path).items() == [AAPL]


def test_missing_file_starts_empty(tmp_path: Path) -> None:
    assert wl(tmp_path).items() == []


@pytest.mark.parametrize("blob", ["{corrupt!!", '{"not": "a list"}', '[{"no_symbol": 1}]'])
def test_corrupt_file_starts_empty_and_recovers(tmp_path: Path, blob: str) -> None:
    path = tmp_path / "watchlist.json"
    path.write_text(blob)
    w = Watchlist(path)
    assert w.items() == []               # never crashes on corruption
    assert w.add(AAPL) is True           # and keeps working (rewrites the file)
    assert Watchlist(path).items() == [AAPL]


def test_stored_shape(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.json"
    Watchlist(path).add(AAPL)
    data = json.loads(path.read_text())
    assert isinstance(data, list)
    item = data[0]
    assert item["symbol"] == "AAPL"
    assert item["asset_class"] == "equity"
    assert item["name"] == "Apple Inc."


def test_load_tolerates_missing_venue(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps([
        {"symbol": "AAPL", "asset_class": "equity", "name": "Apple Inc."},
        {"symbol": "coinbase:BTC-USD", "asset_class": "crypto", "name": "Bitcoin · Coinbase"},
    ]))
    items = Watchlist(path).items()
    assert items[0].venue == "us"                 # equity fallback
    assert items[1].venue == "coinbase"           # derived from canonical prefix


# --- atomicity ----------------------------------------------------------------

def test_atomic_write_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "watchlist.json"
    w = Watchlist(path)
    w.add(AAPL)
    before = path.read_text()

    def boom(src: object, dst: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("entropy.data.watchlist.os.replace", boom)
    with pytest.raises(OSError, match="simulated"):
        w.add(MSFT)
    assert path.read_text() == before             # original file intact
    monkeypatch.undo()
    assert Watchlist(path).items() == [AAPL]      # disk state consistent
