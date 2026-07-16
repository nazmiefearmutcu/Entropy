import asyncio

import pytest

from entropy.app import AppConfig
from entropy.strategy.engine import Bar, Side
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.console import AlgoConsole


@pytest.mark.asyncio
async def test_spy_strategy_warm_and_watching_line_on_mount():
    # crypto disabled -> no network; SPY warms from synthesized sim bars.
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.strategy.is_warm
        console = app.query_one("#console", AlgoConsole)
        # warmup INFO + watching [SPY] INFO both pushed at startup.
        assert console.line_count >= 2
        await pilot.press("q")


@pytest.mark.asyncio
async def test_reconnect_noise_pushes_info_line():
    app = EntropyApp(AppConfig(enable_crypto=False))
    async with app.run_test() as pilot:
        await pilot.pause()
        console = app.query_one("#console", AlgoConsole)
        before = console.line_count
        app._feed_status("connecting…")
        await pilot.pause()
        assert console.line_count > before
        await pilot.press("q")


# --- live-source equity warmup (no network: feed + warmup both stubbed) --------

class _FakePlan:
    provider_name = "stub_provider"
    trimmed_symbols: list[str] = []


def _stub_live_feed(monkeypatch) -> None:
    """start_equity_feed stand-in: an idle never-ticking task + plan."""

    async def stub(sink, symbols):
        async def idle() -> None:
            await asyncio.sleep(3600)

        return asyncio.get_running_loop().create_task(idle()), _FakePlan()

    monkeypatch.setattr("entropy.ui.app.start_equity_feed", stub)


def _console_text(app: EntropyApp) -> str:
    console = app.query_one("#console", AlgoConsole)
    return "\n".join(strip.text for strip in console.lines)


@pytest.mark.asyncio
async def test_live_warmup_failure_falls_back_to_synth_and_boots(monkeypatch):
    _stub_live_feed(monkeypatch)

    async def boom(symbol, interval="15m", limit=64):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", boom)
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="live"))
    async with app.run_test(size=(120, 40)) as pilot:
        text = ""
        for _ in range(40):  # feed worker -> warmup worker settle asynchronously
            await pilot.pause()
            text = _console_text(app)
            if "equity warmup failed" in text:
                break
        assert "equity warmup failed (yahoo down); using synthetic bars" in text
        assert app.strategy.is_warm            # mount-time synth warmup preserved
        assert app._equity_source_resolved == "live"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_live_warmup_seeds_strategy_and_candles_from_real_bars(monkeypatch):
    _stub_live_feed(monkeypatch)
    calls: list[tuple[str, str]] = []
    bar_ns = 15 * 60 * 1_000_000_000
    fabricated = [Bar(ts_ns=i * bar_ns, close=512.0, high=513.0, low=511.0)
                  for i in range(1, 25)]  # 24 bars > slow EMA period (21)

    async def fake(symbol, interval="15m", limit=64):
        calls.append((symbol, interval))
        return fabricated

    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", fake)
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="live"))
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if calls and app.strategy._slow.value == 512.0:
                break
        assert calls == [("SPY", "15m")]        # symbol + active timeframe
        assert app.strategy.is_warm
        # EMA over constant-close real bars converges exactly to their close —
        # proof the strategy was reseeded from the fetched bars, not synth ones.
        assert app.strategy._slow.value == 512.0
        candles = app._price_candles.bars()
        assert len(candles) == 24               # SPY chart seeded from the bars
        assert candles[-1].c == 512.0
        assert candles[-1].h == 513.0
        assert candles[-1].l == 511.0
        assert "watching [SPY]" in _console_text(app)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_live_warmup_rebuild_carries_open_position(monkeypatch):
    """A live tick can open a position (journaled to the trade CSV) while the
    Yahoo fetch is in flight; the strategy rebuild must transplant it so the
    OPEN row can still be closed later — only the EMAs reseed from real bars."""
    _stub_live_feed(monkeypatch)
    bar_ns = 15 * 60 * 1_000_000_000
    fabricated = [Bar(ts_ns=i * bar_ns, close=512.0) for i in range(1, 25)]
    apps: list[EntropyApp] = []

    async def delayed(symbol, interval="15m", limit=64):
        # Simulate a tick landing mid-fetch: the mount-time synth warmup left
        # _prev_sign == 0 (flat bars), so one up-tick crosses and opens a LONG.
        strat = apps[0].strategy
        strat.on_price(apps[0].cfg.strategy_symbol, 1000.0, 123)
        assert strat.position.side is Side.LONG  # precondition for the test
        return fabricated

    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", delayed)
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="live"))
    apps.append(app)
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(40):
            await pilot.pause()
            if app.strategy._slow.value == 512.0:
                break
        # Strategy was rebuilt and reseeded from the real bars…
        assert app.strategy.is_warm
        assert app.strategy._slow.value == 512.0
        # …but the open position survived the rebuild instead of being orphaned.
        assert app.strategy.position.side is Side.LONG
        assert app.strategy.position.entry_px == 1000.0
        # A downward cross on the fresh EMAs still closes the carried position.
        events = []
        for px in (400.0, 380.0, 360.0, 340.0, 320.0, 300.0):
            events += app.strategy.on_price("SPY", px, 456)
        kinds = [e.kind.value for e in events]
        assert "close_long" in kinds
        await pilot.press("q")
