# tests/ui/test_feed_toggles.py
"""Settings 'Enable Equities' / 'Enable Crypto' switches: all four ON/OFF
transitions must work for the app's whole lifetime, not only at mount.
Feeds are stubbed — no network."""
from __future__ import annotations

import asyncio

import pytest

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.console import AlgoConsole


def _console_text(app: EntropyApp) -> str:
    console = app.query_one("#console", AlgoConsole)
    return "\n".join(strip.text for strip in console.lines)


def _apply(app: EntropyApp, **over: object) -> None:
    base: dict[str, object] = dict(
        theme=app.cfg.theme, chart_type=app.cfg.chart_type, show_volume=app.cfg.show_volume,
        timeframe=app.cfg.timeframe, enable_equities=app.cfg.enable_equities,
        enable_crypto=app.cfg.enable_crypto, equity_source=app.cfg.equity_source,
        equity_tps=app.cfg.equity_tps, strategy_symbol=app.cfg.strategy_symbol,
        crypto_strategy_symbol=app.cfg.crypto_strategy_symbol,
        spike_pct=app.cfg.engine.spike_pct, snapdrop_pct=app.cfg.engine.snapdrop_pct,
    )
    base.update(over)
    app._apply_settings(**base)  # type: ignore[arg-type]


def _equity_workers(app: EntropyApp) -> list:
    return [w for w in app.workers if w.group == "equity_feed"]


def _stub_crypto(monkeypatch, started: list, calls: list) -> None:
    """start_feed stand-in returning an idle collect task; klines warmup stubbed."""

    async def stub_start_feed(sink):
        calls.append(sink)

        async def idle() -> None:
            await asyncio.sleep(3600)

        task = asyncio.get_running_loop().create_task(idle())
        started.append(task)
        return task

    async def no_klines(symbol, interval="15m", **kw):
        return []

    monkeypatch.setattr("entropy.ui.app.start_feed", stub_start_feed)
    monkeypatch.setattr("entropy.ui.app.warmup_klines", no_klines)


@pytest.mark.asyncio
async def test_equities_toggle_on_to_off_cancels_feed_worker():
    app = EntropyApp(AppConfig(enable_crypto=False, equity_source="sim", equity_tps=10))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert len(_equity_workers(app)) == 1

        _apply(app, enable_equities=False)
        for _ in range(20):  # worker-group cancel propagates asynchronously
            ws = _equity_workers(app)
            if not ws or all(w.is_cancelled or w.is_finished for w in ws):
                break
            await pilot.pause()
        ws = _equity_workers(app)
        assert not ws or all(w.is_cancelled or w.is_finished for w in ws)
        assert app.cfg.enable_equities is False
        assert "equities: feed disabled" in _console_text(app)


@pytest.mark.asyncio
async def test_equities_toggle_off_to_on_launches_feed():
    app = EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False, equity_source="sim", equity_tps=10
    ))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert _equity_workers(app) == []  # boot honored the OFF switch

        _apply(app, enable_equities=True)
        for _ in range(20):
            if "equities: source=sim" in _console_text(app):
                break
            await pilot.pause()
        assert app.cfg.enable_equities is True
        assert "equities: feed enabled" in _console_text(app)
        assert "equities: source=sim" in _console_text(app)
        assert len(_equity_workers(app)) == 1


@pytest.mark.asyncio
async def test_crypto_toggle_on_to_off_cancels_worker_and_collect_task(monkeypatch):
    started: list[asyncio.Task] = []
    _stub_crypto(monkeypatch, started, [])
    app = EntropyApp(AppConfig(enable_equities=False, enable_crypto=True))
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(20):
            if started:
                break
            await pilot.pause()
        assert len(started) == 1

        _apply(app, enable_crypto=False)
        for _ in range(20):  # cancel_group -> worker finally -> collect task dies
            if started[0].cancelled():
                break
            await pilot.pause()
        assert started[0].cancelled()
        assert app.cfg.enable_crypto is False
        assert "crypto: feed disabled" in _console_text(app)


@pytest.mark.asyncio
async def test_crypto_toggle_off_to_on_launches_feed(monkeypatch):
    started: list[asyncio.Task] = []
    calls: list = []
    _stub_crypto(monkeypatch, started, calls)
    app = EntropyApp(AppConfig(enable_equities=False, enable_crypto=False))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert calls == []  # boot honored the OFF switch

        _apply(app, enable_crypto=True)
        for _ in range(20):
            if calls:
                break
            await pilot.pause()
        assert len(calls) == 1
        assert calls[0] is app._sink
        assert app.cfg.enable_crypto is True
        assert "crypto: feed enabled" in _console_text(app)
