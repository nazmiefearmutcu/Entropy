import pytest

from entropy.app import AppConfig
from entropy.engine.timeframe import get_timeframe
from entropy.strategy.engine import Bar
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.charts import PriceChart

BAR_NS = get_timeframe("15m").bar_ns


def _app(tmp_path) -> EntropyApp:
    return EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False,
        watchlist_path=str(tmp_path / "watchlist.json"),
    ))


def _bars(n: int = 24) -> list[Bar]:
    return [Bar(ts_ns=i * BAR_NS, close=10.0 + i, high=11.0 + i, low=9.0 + i)
            for i in range(1, n + 1)]


@pytest.mark.asyncio
async def test_default_focus_and_titles_on_mount(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.focus_symbol == "binance-spot:BTCUSDT"
        assert app.query_one("#price", PriceChart).title == "binance-spot:BTCUSDT · 15m"
        assert app.query_one("#price2", PriceChart).title == "SPY · 15m"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_crypto_focus_swaps_aggregator_and_warms_from_klines(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_klines(symbol, interval="1m", limit=200):
        calls.append((symbol, interval))
        return _bars()

    monkeypatch.setattr("entropy.ui.app.warmup_klines", fake_klines)
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        old = app._focus_candles
        app.focus_symbol = "binance-spot:ETHUSDT"
        for _ in range(40):
            await pilot.pause()
            if calls and app._focus_candles.bars():
                break
        assert calls == [("ETHUSDT", "15m")]     # raw symbol part + active timeframe
        assert app._focus_candles is not old     # fresh aggregator swapped in
        assert len(app._focus_candles.bars()) == 24
        assert app.query_one("#price", PriceChart).title == "binance-spot:ETHUSDT · 15m"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_equity_focus_warms_only_when_live(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_equity(symbol, interval="15m", limit=64):
        calls.append((symbol, interval))
        return [Bar(ts_ns=i * BAR_NS, close=50.0) for i in range(1, 25)]

    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", fake_equity)
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Sim-resolved source: no history source for sim symbols — skip warmup,
        # start empty, and let live ticks fill the chart.
        app._equity_source_resolved = "sim"
        old = app._focus_candles
        app.focus_symbol = "MSFT"
        for _ in range(10):
            await pilot.pause()
        assert calls == []
        assert app._focus_candles is not old
        assert len(app._focus_candles.bars()) == 0
        assert app.query_one("#price", PriceChart).title == "MSFT · 15m"

        # Live-resolved source: bare equity tickers warm from equity bars.
        app._equity_source_resolved = "live"
        app.focus_symbol = "AAPL"
        for _ in range(40):
            await pilot.pause()
            if calls and app._focus_candles.bars():
                break
        assert calls == [("AAPL", "15m")]
        assert len(app._focus_candles.bars()) == 24
        assert app.query_one("#price", PriceChart).title == "AAPL · 15m"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_focus_warmup_failure_notes_console_and_survives(tmp_path, monkeypatch):
    async def boom(symbol, interval="1m", limit=200):
        raise RuntimeError("binance down")

    monkeypatch.setattr("entropy.ui.app.warmup_klines", boom)
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.focus_symbol = "binance-spot:ETHUSDT"
        text = ""
        for _ in range(40):
            await pilot.pause()
            from entropy.ui.widgets.console import AlgoConsole

            console = app.query_one("#console", AlgoConsole)
            text = "\n".join(strip.text for strip in console.lines)
            if "focus warmup failed" in text:
                break
        assert "focus warmup failed (binance down)" in text
        assert "binance down" in app._error_text
        assert app.focus_symbol == "binance-spot:ETHUSDT"   # app kept running
        await pilot.press("q")


def _trade(symbol: str, price: float):
    from crypcodile.schema.records import Side, Trade

    return Trade(exchange="test", symbol=symbol, symbol_raw=symbol, exchange_ts=None,
                 local_ts=1_000_000, id="t1", price=price, amount=1.0, side=Side.BUY)


@pytest.mark.asyncio
async def test_focus_routes_trades_to_chart_one(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._equity_source_resolved = "sim"
        app.focus_symbol = "NVDA"
        await pilot.pause()
        app._route_candle(_trade("NVDA", 10.0))    # focus -> chart #1
        app._route_candle(_trade("SPY", 20.0))     # strategy -> chart #2
        app._route_candle(_trade("AMD", 30.0))     # neither -> dropped
        assert len(app._focus_candles.bars()) == 1
        assert app._focus_candles.bars()[0].c == 10.0
        assert len(app._price_candles.bars()) == 1
        assert app._price_candles.bars()[0].c == 20.0
        await pilot.press("q")
