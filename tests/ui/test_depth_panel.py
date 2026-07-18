"""DepthPanel: pure ladder-layout helpers, the DepthProfile->DepthView fetch
mapping, and (below) the app's lazy TTL-cached depth-fetch pipeline."""
from __future__ import annotations

import msgspec
import pytest

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.console import AlgoConsole
from entropy.ui.widgets.depth_panel import (
    DepthPanel,
    DepthView,
    _bar,
    depth_rows,
    fetch_depth,
)

S = 1_000_000_000


# --- pure ladder layout --------------------------------------------------------

def _kinds(rows) -> list[str]:
    return [r.kind for r in rows]


def test_depth_rows_none_and_empty_show_placeholder():
    rows = depth_rows(None)
    assert _kinds(rows) == ["badge", "empty"]
    assert rows[0].text == "DEPTH —"                     # no view, no symbol -> dash
    empty = DepthView(symbol="AAPL", basis="yahoo_1m_vap")
    rows = depth_rows(empty)
    assert _kinds(rows) == ["badge", "empty"]
    assert "AAPL" in rows[0].text and rows[1].text == "—"


def test_depth_rows_symbol_override_names_focus_while_loading():
    # view is None (fetch in flight) but the symbol IS known: the badge still
    # names the focus symbol rather than collapsing to a bare dash.
    rows = depth_rows(None, symbol="TSLA")
    assert rows[0].text == "DEPTH TSLA"
    assert rows[1].text == "—"


def test_depth_rows_synthetic_badge_and_dom_ordering():
    view = DepthView(
        symbol="AAPL", basis="yahoo_1m_vap", is_synthetic=True, reference_price=100.0,
        bids=((99.0, 5.0), (98.0, 3.0)), asks=((101.0, 4.0), (102.0, 8.0)),
    )
    rows = depth_rows(view)
    assert _kinds(rows) == ["badge", "ask", "ask", "mid", "bid", "bid"]
    assert "SYNTH·yahoo_1m_vap" in rows[0].text
    # Asks render highest-at-top (102 above 101); bids highest-just-below-mid.
    assert "102.00" in rows[1].text and "101.00" in rows[2].text
    assert "100.00" in rows[3].text and "rel.liq" in rows[3].text
    assert "99.00" in rows[4].text and "98.00" in rows[5].text


def test_depth_rows_real_l1_badge_and_spread():
    view = DepthView(
        symbol="AAPL", basis="alpaca_l1", is_synthetic=False, reference_price=100.05,
        bids=((100.0, 200.0),), asks=((100.1, 150.0),),
    )
    rows = depth_rows(view)
    assert _kinds(rows) == ["badge", "ask", "mid", "bid"]
    assert "L1·alpaca_l1" in rows[0].text
    # Real L1 shows a genuine spread (best_ask - best_bid), not the rel.liq note.
    assert "spread 0.10" in rows[2].text and "rel.liq" not in rows[2].text


def test_depth_rows_truncates_to_max_levels():
    view = DepthView(
        symbol="X", basis="yahoo_1m_vap", reference_price=50.0,
        bids=tuple((49.0 - i, 1.0) for i in range(10)),
        asks=tuple((51.0 + i, 1.0) for i in range(10)),
    )
    rows = depth_rows(view, max_levels=3)
    assert _kinds(rows) == ["badge", "ask", "ask", "ask", "mid", "bid", "bid", "bid"]


def test_bar_scales_against_max_and_never_zero_for_positive():
    assert _bar(0.0, 10.0) == ""            # nothing for a zero level
    assert _bar(10.0, 10.0) == "█" * 12     # full width at the max
    assert _bar(0.01, 10.0) == "█"          # a tiny positive size still shows one block
    assert len(_bar(5.0, 10.0)) == 6        # half the max -> half the bar


# --- DepthProfile -> DepthView fetch mapping -----------------------------------

@pytest.mark.asyncio
async def test_fetch_depth_maps_profile(monkeypatch):
    from stockodile.schema.records import DepthProfile

    class FakeSource:
        async def snapshot(self, symbol: str) -> DepthProfile:
            return DepthProfile(
                provider="synth", symbol=f"synth:{symbol}", symbol_raw=symbol,
                local_ts=1, bids=[(99.0, 5.0)], asks=[(101.0, 4.0)],
                reference_price=100.0, basis="yahoo_1m_vap", is_synthetic=True, depth=2,
            )

    monkeypatch.setattr(
        "stockodile.depth.select_depth_source", lambda **kw: FakeSource()
    )
    view = await fetch_depth("aapl")
    assert view == DepthView(
        symbol="AAPL", basis="yahoo_1m_vap", is_synthetic=True,
        reference_price=100.0, bids=((99.0, 5.0),), asks=((101.0, 4.0),),
    )


@pytest.mark.asyncio
async def test_fetch_depth_empty_levels_returns_none(monkeypatch):
    from stockodile.schema.records import DepthProfile

    class EmptySource:
        async def snapshot(self, symbol: str) -> DepthProfile:
            return DepthProfile(
                provider="synth", symbol="synth:X", symbol_raw="X", local_ts=1,
                bids=[], asks=[], reference_price=0.0, basis="yahoo_1m_vap",
                is_synthetic=True, depth=0,
            )

    monkeypatch.setattr(
        "stockodile.depth.select_depth_source", lambda **kw: EmptySource()
    )
    assert await fetch_depth("X") is None


# --- app pipeline: lazy fetch / TTL / failure / gating ---------------------------

def _app(tmp_path) -> EntropyApp:
    return EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False,
        watchlist_path=str(tmp_path / "watchlist.json"),
    ))


def _live_depth_app(app: EntropyApp) -> None:
    """Put the app in the depth-eligible state: equities enabled, source live,
    and the (hidden-by-default) panel made visible."""
    app.cfg = msgspec.structs.replace(app.cfg, enable_equities=True)
    app._equity_source_resolved = "live"
    app.query_one("#depth", DepthPanel).display = True


def _console_text(app: EntropyApp) -> str:
    console = app.query_one("#console", AlgoConsole)
    return "\n".join(strip.text for strip in console.lines)


def _sample_view() -> DepthView:
    return DepthView(
        symbol="AAPL", basis="yahoo_1m_vap", is_synthetic=True, reference_price=100.0,
        bids=((99.0, 5.0),), asks=((101.0, 4.0),),
    )


@pytest.mark.asyncio
async def test_depth_fetched_once_and_rendered(tmp_path, monkeypatch):
    async def no_bars(symbol, interval="15m", limit=64):
        return []
    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", no_bars)

    calls: list[str] = []

    async def fake_fetch(symbol: str) -> DepthView:
        calls.append(symbol)
        return _sample_view()

    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        _live_depth_app(app)
        app._depth_fetcher = fake_fetch
        app.focus_symbol = "AAPL"
        app.sample_snapshot()                           # kicks the worker
        panel = app.query_one("#depth", DepthPanel)
        assert panel.view is None                       # placeholder while loading
        plain_loading = panel.render().plain
        assert "DEPTH AAPL" in plain_loading            # badge names focus while loading
        assert "—" in plain_loading                     # ladder placeholder row
        for _ in range(40):
            await pilot.pause()
            if calls:
                break
        app.sample_snapshot()                           # re-read from the cache
        assert calls == ["AAPL"]
        assert panel.view == _sample_view()
        plain = panel.render().plain
        assert "SYNTH·yahoo_1m_vap" in plain and "AAPL" in plain
        assert "101.00" in plain and "99.00" in plain   # ask above, bid below
        # further snapshots inside the TTL never refetch
        app.sample_snapshot()
        app.sample_snapshot()
        await pilot.pause()
        assert calls == ["AAPL"]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_depth_ttl_respected_with_fake_clock(tmp_path, monkeypatch):
    async def no_bars(symbol, interval="15m", limit=64):
        return []
    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", no_bars)

    calls: list[str] = []
    clock = {"t": 1000.0}

    async def fake_fetch(symbol: str) -> DepthView:
        calls.append(symbol)
        return _sample_view()

    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        _live_depth_app(app)
        app._depth_fetcher = fake_fetch
        app._depth_now = lambda: clock["t"]
        app.focus_symbol = "AAPL"
        app.sample_snapshot()
        for _ in range(40):
            await pilot.pause()
            if calls:
                break
        assert calls == ["AAPL"]
        clock["t"] += 19.0                              # still inside DEPTH_TTL_S (20s)
        app.sample_snapshot()
        await pilot.pause()
        assert calls == ["AAPL"]
        clock["t"] += 2.0                               # 21s total: expired
        app.sample_snapshot()
        for _ in range(40):
            await pilot.pause()
            if len(calls) > 1:
                break
        assert calls == ["AAPL", "AAPL"]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_depth_failure_is_silent_and_rate_limited(tmp_path, monkeypatch):
    async def no_bars(symbol, interval="15m", limit=64):
        return []
    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", no_bars)

    calls: list[str] = []

    async def boom(symbol: str) -> DepthView:
        calls.append(symbol)
        raise RuntimeError("yahoo said 429")

    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        _live_depth_app(app)
        app._depth_fetcher = boom
        app.focus_symbol = "AAPL"
        app.sample_snapshot()
        for _ in range(40):
            await pilot.pause()
            if calls:
                break
        app.sample_snapshot()                           # cached failure: no retry
        app.sample_snapshot()
        await pilot.pause()
        assert calls == ["AAPL"]
        panel = app.query_one("#depth", DepthPanel)
        assert panel.view is None                       # placeholder stays up
        assert "—" in panel.render().plain
        assert "yahoo said 429" not in _console_text(app)  # silent by contract
        assert app.focus_symbol == "AAPL"               # app alive and unchanged
        await pilot.press("q")


@pytest.mark.asyncio
async def test_no_depth_fetch_when_hidden_or_crypto_or_sim(tmp_path):
    calls: list[str] = []

    async def fake_fetch(symbol: str) -> DepthView:
        calls.append(symbol)
        return _sample_view()

    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._depth_fetcher = fake_fetch
        # (1) live equity focus but panel HIDDEN (default): no fetch
        app.cfg = msgspec.structs.replace(app.cfg, enable_equities=True)
        app._equity_source_resolved = "live"
        app.focus_symbol = "NVDA"
        app.sample_snapshot()
        # (2) panel visible but crypto focus: no fetch
        app.query_one("#depth", DepthPanel).display = True
        app.focus_symbol = "binance-spot:BTCUSDT"
        app.sample_snapshot()
        # (3) panel visible, equity, but SIM source: no fetch
        app._equity_source_resolved = "sim"
        app.focus_symbol = "NVDA"
        app.sample_snapshot()
        for _ in range(10):
            await pilot.pause()
        assert calls == []
        await pilot.press("q")


@pytest.mark.asyncio
async def test_depth_command_toggles_and_focuses(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one("#depth", DepthPanel)
        assert panel.display is False                   # hidden by default
        assert app._execute_command("depth") is True    # toggle on
        assert panel.display is True and app.cfg.show_depth is True
        assert app._execute_command("depth") is True    # toggle off
        assert panel.display is False and app.cfg.show_depth is False
        assert app._execute_command("depth aapl") is True  # focus + show
        assert panel.display is True
        assert app.focus_symbol == "AAPL"               # normalized
        await pilot.press("q")
