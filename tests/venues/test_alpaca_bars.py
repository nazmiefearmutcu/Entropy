"""fetch_bars: kline-shaped rows on both tiers (Alpaca MarketData v2 keyed,
Yahoo chart keyless fallback) — zero network via FakeTransport.
"""
from __future__ import annotations

import pytest

from entropy.venues import AlpacaEquitiesAdapter, VenueError

from .conftest import FakeTransport, alpaca_routes, make_alpaca

YAHOO_PAYLOAD = {
    "chart": {"result": [{
        "meta": {"symbol": "AAPL"},
        "timestamp": [1717416600, 1717417500, 1717418400],
        "indicators": {"quote": [{
            "open": [100.0, 101.0, None],
            "high": [101.0, 102.0, None],
            "low": [99.5, 100.5, None],
            "close": [100.5, 101.5, None],
            "volume": [1000, 2000, None],
        }]},
    }], "error": None},
}

ALPACA_BARS = {"bars": [
    {"t": "2024-06-03T13:30:00Z", "o": 100.0, "h": 101.0, "l": 99.5,
     "c": 100.5, "v": 1000, "n": 10, "vw": 100.2},
    {"t": "2024-06-03T13:45:00Z", "o": 101.0, "h": 102.0, "l": 100.5,
     "c": 101.5, "v": 2000, "n": 20, "vw": 101.2},
], "next_page_token": None}


def _assert_kline_rows(rows: list[list]) -> None:
    """Binance kline layout/types: [0] int open-ms, [1..5] floats,
    [6] int close-ms; ascending; 15m bar span."""
    assert rows == sorted(rows, key=lambda r: r[0])
    for r in rows:
        assert isinstance(r[0], int) and isinstance(r[6], int)
        assert r[6] == r[0] + 900_000 - 1
        for v in r[1:6]:
            assert isinstance(v, float)


def test_alpaca_tier_keyed_returns_kline_rows():
    a, t = make_alpaca(alpaca_routes(bars=ALPACA_BARS))
    rows = a.fetch_bars("AAPL", "15m", 200)
    _assert_kline_rows(rows)
    assert len(rows) == 2
    assert rows[0][0] == 1717421400000
    assert rows[0][1:6] == [100.0, 101.0, 99.5, 100.5, 1000.0]
    assert any("/v2/stocks/AAPL/bars" in c["url"] and "feed=iex"
               in c["url"] and "timeframe=15Min" in c["url"]
               for c in t.calls if c["method"] == "GET")


def test_no_keys_falls_back_to_keyless_yahoo():
    t = FakeTransport([("GET", "query1.finance.yahoo.com", 200,
                        YAHOO_PAYLOAD)])
    a = AlpacaEquitiesAdapter(transport=t)  # NO keys
    rows = a.fetch_bars("AAPL", "15m", 200)
    _assert_kline_rows(rows)
    # None-valued bar dropped; ascending order preserved
    assert len(rows) == 2
    assert rows[1][1:6] == [101.0, 102.0, 100.5, 101.5, 2000.0]


def test_keyed_alpaca_failure_falls_back_to_yahoo():
    routes = [("GET", "/v2/stocks/AAPL/bars", 502, {"message": "down"}),
              ("GET", "query1.finance.yahoo.com", 200, YAHOO_PAYLOAD)]
    a, _ = make_alpaca(routes)
    rows = a.fetch_bars("AAPL", "15m", 200)
    _assert_kline_rows(rows)
    assert len(rows) == 2


def test_both_tiers_failing_raises_venue_error():
    a, _ = make_alpaca([("GET", "/v2/stocks/AAPL/bars", 500, {}),
                        ("GET", "query1.finance.yahoo.com", 429, {})])
    with pytest.raises(VenueError):
        a.fetch_bars("AAPL", "15m", 200)


def test_malformed_yahoo_payload_raises():
    a, _ = make_alpaca([("GET", "/v2/stocks/AAPL/bars", 404, {}),
                        ("GET", "query1.finance.yahoo.com", 200,
                         {"chart": {"result": []}})])
    with pytest.raises(VenueError):
        a.fetch_bars("AAPL", "15m", 200)


def test_yahoo_range_ladder_and_prepost_excluded():
    t = FakeTransport([("GET", "query1.finance.yahoo.com", 200,
                        YAHOO_PAYLOAD)])
    a = AlpacaEquitiesAdapter(transport=t)
    a.fetch_bars("AAPL", "15m", 200)
    url = t.calls[0]["url"]
    assert "range=5d" in url          # 200 bars * 15m ≈ 3 days -> "5d"
    assert "includePrePost=false" in url
    assert "interval=15m" in url
    a.fetch_bars("AAPL", "15m", 600)  # 600 bars ≈ 9 days -> "1mo"
    assert "range=1mo" in t.calls[1]["url"]


def test_end_ms_bounds_the_alpaca_request():
    a, t = make_alpaca(alpaca_routes(bars=ALPACA_BARS))
    a.fetch_bars("AAPL", "15m", 100, end_ms=1717500000000)
    url = [c["url"] for c in t.calls if "bars" in c["url"]][0]
    assert "end=2024-06-04T11%3A20%3A00Z" in url
