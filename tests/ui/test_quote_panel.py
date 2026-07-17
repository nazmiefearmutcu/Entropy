"""QuotePanel: engine-backed quote lines, lazy TTL-cached fundamentals, and
the Engine.session_range accessor regression tests."""
from __future__ import annotations

import msgspec
import pytest

from entropy.app import AppConfig
from entropy.engine.engine import Engine
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.console import AlgoConsole
from entropy.ui.widgets.quote_panel import (
    Fundamentals,
    QuotePanel,
    format_compact,
    fundamentals_line,
)

S = 1_000_000_000


# --- Engine.session_range (regression) ----------------------------------------

def test_session_range_unseen_symbol_is_none():
    assert Engine().session_range("AAA") is None


def test_session_range_tracks_session_hi_lo():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.on_trade("AAA", 110.0, 1.0, "buy", S)
    e.on_trade("AAA", 90.0, 1.0, "sell", 2 * S)
    e.on_trade("AAA", 95.0, 1.0, "buy", 3 * S)   # inside the range: no change
    assert e.session_range("AAA") == (110.0, 90.0)


def test_session_range_is_read_only():
    # Calling the accessor must not mutate tape state: identical back-to-back
    # results, unchanged quote, and an unchanged snapshot.
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    e.on_trade("AAA", 105.0, 1.0, "buy", S)
    before_quote = e.quote("AAA")
    snap_before = e.snapshot()
    assert e.session_range("AAA") == e.session_range("AAA") == (105.0, 100.0)
    assert e.quote("AAA") == before_quote
    assert e.snapshot() == snap_before


def test_session_range_single_trade_collapses_to_point():
    e = Engine()
    e.on_trade("AAA", 42.0, 1.0, "buy", 0)
    assert e.session_range("AAA") == (42.0, 42.0)


# --- formatting helpers ---------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (3.42e12, "3.42T"),
    (456.7e9, "456.70B"),
    (12.3e6, "12.30M"),
    (9_500.0, "9.50K"),
    (123.45, "123.45"),
])
def test_format_compact(value, expected):
    assert format_compact(value) == expected


def test_fundamentals_line_placeholders_and_values():
    assert fundamentals_line(None) == "P/E — · MktCap — · 52w —/—"
    line = fundamentals_line(
        Fundamentals(pe=34.21, market_cap=3.2e12, high_52w=198.32, low_52w=124.17)
    )
    assert line == "P/E 34.2 · MktCap 3.20T · 52w 198.32/124.17"
    partial = fundamentals_line(Fundamentals(pe=10.0))
    assert partial == "P/E 10.0 · MktCap — · 52w —/—"


# --- pilot: panel state from a warmed engine ------------------------------------

def _app(tmp_path) -> EntropyApp:
    return EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False,
        watchlist_path=str(tmp_path / "watchlist.json"),
    ))


def _console_text(app: EntropyApp) -> str:
    console = app.query_one("#console", AlgoConsole)
    return "\n".join(strip.text for strip in console.lines)


@pytest.mark.asyncio
async def test_panel_renders_quote_from_warmed_engine(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.focus_symbol = "NVDA"                     # sim equity: no warmup fetch
        app.engine.on_trade("NVDA", 100.0, 1.0, "buy", 0)
        app.engine.on_trade("NVDA", 110.0, 1.0, "buy", S)
        app.engine.on_trade("NVDA", 90.0, 1.0, "sell", 2 * S)
        app.sample_snapshot()
        panel = app.query_one("#quote", QuotePanel)
        s = panel.state
        assert s.symbol == "NVDA"
        assert s.asset == "SIM"                       # equity without live source
        assert s.last == 90.0
        assert s.pct == pytest.approx(-10.0)
        assert (s.hi, s.lo) == (110.0, 90.0)
        assert s.show_fundamentals is False           # equities on live only
        plain = panel.render().plain
        assert "NVDA" in plain and "SIM" in plain
        assert "90.00" in plain and "-10.00%" in plain
        assert "Hi 110.00" in plain and "Lo 90.00" in plain
        assert "P/E" not in plain
        await pilot.press("q")


@pytest.mark.asyncio
async def test_panel_crypto_chip_and_unseen_placeholders(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.sample_snapshot()                          # default focus = crypto
        panel = app.query_one("#quote", QuotePanel)
        s = panel.state
        assert s.symbol == "binance-spot:BTCUSDT"
        assert s.asset == "CRYPTO"
        assert s.last is None and s.hi is None         # engine hasn't seen it
        assert s.show_fundamentals is False
        assert "—" in panel.render().plain             # placeholders rendered
        await pilot.press("q")


# --- pilot: fundamentals lazy fetch / TTL / failure ------------------------------

def _live_equity_app(app: EntropyApp) -> None:
    """Put the app in the fundamentals-eligible state: equities enabled
    (config flag only — no feed was launched) with the source resolved live."""
    app.cfg = msgspec.structs.replace(app.cfg, enable_equities=True)
    app._equity_source_resolved = "live"


@pytest.mark.asyncio
async def test_fundamentals_fetched_once_and_rendered(tmp_path, monkeypatch):
    async def no_bars(symbol, interval="15m", limit=64):
        return []                                     # neutralize focus warmup
    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", no_bars)

    calls: list[str] = []

    async def fake_fetch(symbol: str) -> Fundamentals:
        calls.append(symbol)
        return Fundamentals(pe=30.0, market_cap=3e12, high_52w=200.0, low_52w=100.0)

    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        _live_equity_app(app)
        app._fundamentals_fetcher = fake_fetch
        app.focus_symbol = "AAPL"
        app.sample_snapshot()                          # kicks the worker
        panel = app.query_one("#quote", QuotePanel)
        assert panel.state.show_fundamentals is True
        assert panel.state.fundamentals is None        # placeholders while loading
        assert "P/E —" in panel.render().plain
        for _ in range(40):
            await pilot.pause()
            if calls:
                break
        app.sample_snapshot()                          # re-read from the cache
        assert calls == ["AAPL"]
        assert panel.state.fundamentals == Fundamentals(
            pe=30.0, market_cap=3e12, high_52w=200.0, low_52w=100.0
        )
        assert "P/E 30.0 · MktCap 3.00T · 52w 200.00/100.00" in panel.render().plain
        # further snapshots inside the TTL never refetch
        app.sample_snapshot()
        app.sample_snapshot()
        await pilot.pause()
        assert calls == ["AAPL"]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_fundamentals_ttl_respected_with_fake_clock(tmp_path, monkeypatch):
    async def no_bars(symbol, interval="15m", limit=64):
        return []
    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", no_bars)

    calls: list[str] = []
    clock = {"t": 1000.0}

    async def fake_fetch(symbol: str) -> Fundamentals:
        calls.append(symbol)
        return Fundamentals(pe=1.0)

    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        _live_equity_app(app)
        app._fundamentals_fetcher = fake_fetch
        app._fundamentals_now = lambda: clock["t"]
        app.focus_symbol = "AAPL"
        app.sample_snapshot()
        for _ in range(40):
            await pilot.pause()
            if calls:
                break
        assert calls == ["AAPL"]
        clock["t"] += 599.0                            # still inside the TTL
        app.sample_snapshot()
        await pilot.pause()
        assert calls == ["AAPL"]
        clock["t"] += 2.0                              # 601s total: expired
        app.sample_snapshot()
        for _ in range(40):
            await pilot.pause()
            if len(calls) > 1:
                break
        assert calls == ["AAPL", "AAPL"]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_fundamentals_failure_is_silent_and_rate_limited(tmp_path, monkeypatch):
    async def no_bars(symbol, interval="15m", limit=64):
        return []
    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", no_bars)

    calls: list[str] = []

    async def boom(symbol: str) -> Fundamentals:
        calls.append(symbol)
        raise RuntimeError("google said no")

    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        _live_equity_app(app)
        app._fundamentals_fetcher = boom
        app.focus_symbol = "AAPL"
        app.sample_snapshot()
        for _ in range(40):
            await pilot.pause()
            if calls:
                break
        app.sample_snapshot()                          # cached failure: no retry
        app.sample_snapshot()
        await pilot.pause()
        assert calls == ["AAPL"]
        panel = app.query_one("#quote", QuotePanel)
        assert panel.state.fundamentals is None        # placeholders stay up
        assert "P/E —" in panel.render().plain
        assert "google said no" not in _console_text(app)   # silent by contract
        assert app.focus_symbol == "AAPL"              # app alive and unchanged
        await pilot.press("q")


@pytest.mark.asyncio
async def test_no_fetch_for_crypto_or_sim_focus(tmp_path):
    calls: list[str] = []

    async def fake_fetch(symbol: str) -> Fundamentals:
        calls.append(symbol)
        return Fundamentals()

    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._fundamentals_fetcher = fake_fetch
        app.sample_snapshot()                          # crypto focus
        app._equity_source_resolved = "sim"
        app.focus_symbol = "NVDA"                      # sim equity focus
        app.sample_snapshot()
        for _ in range(10):
            await pilot.pause()
        assert calls == []
        await pilot.press("q")
