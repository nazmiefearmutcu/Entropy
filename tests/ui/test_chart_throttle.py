"""Chart redraw throttling + EMA overlay wiring through the app's 10 Hz snapshot.

All app-level tests run with both feeds disabled and drive ``sample_snapshot``
manually in synchronous blocks (no awaits between calls), so the interval
timer can never interleave and the redraw counts are deterministic.
"""

import msgspec
import pytest

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.charts import (
    ChartRedrawMemo,
    PriceChart,
    VolumeChart,
    align_overlay,
)

_BASE = 1_700_000_000_000_000_000  # realistic intraday ns timestamp


# ---------------------------------------------------------------- pure memo


def test_memo_first_draw_is_stale():
    memo = ChartRedrawMemo()
    key = (30, 100.0, 5.0, "candlestick")
    assert memo.is_stale("#price", key)


def test_memo_same_key_not_stale_after_record():
    memo = ChartRedrawMemo()
    key = (30, 100.0, 5.0, "candlestick")
    memo.record("#price", key)
    assert not memo.is_stale("#price", key)


def test_memo_any_component_change_is_stale():
    memo = ChartRedrawMemo()
    memo.record("#price", (30, 100.0, "candlestick", "entropy"))
    assert memo.is_stale("#price", (31, 100.0, "candlestick", "entropy"))  # new bar
    assert memo.is_stale("#price", (30, 100.5, "candlestick", "entropy"))  # last close
    assert memo.is_stale("#price", (30, 100.0, "line", "entropy"))         # chart type
    assert memo.is_stale("#price", (30, 100.0, "candlestick", "dracula"))  # theme


def test_memo_charts_are_independent():
    memo = ChartRedrawMemo()
    key = (30, 100.0, "candlestick")
    memo.record("#price", key)
    assert memo.is_stale("#price2", key)


# ------------------------------------------------------------- app plumbing


def _app(tmp_path, **kw):
    return EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False,
        watchlist_path=str(tmp_path / "watchlist.json"),
        trade_csv_path=str(tmp_path / "trades.csv"),
        **kw,
    ))


def _seed_focus(app, n, start_price=100.0):
    """One trade per bar bucket: o == c, so every bar counts as an up bar."""
    step = app._tf.bar_ns
    for i in range(n):
        app._focus_candles.add(_BASE + i * step, start_price + i * 0.1, 1.0)


def _count_replots(monkeypatch):
    calls = {"n": 0}
    orig = PriceChart.replot

    def counting(self):
        calls["n"] += 1
        orig(self)

    monkeypatch.setattr(PriceChart, "replot", counting)
    return calls


@pytest.mark.asyncio
async def test_identical_consecutive_draws_skip_replot(tmp_path, monkeypatch):
    app = _app(tmp_path)
    calls = _count_replots(monkeypatch)
    async with app.run_test(size=(120, 60)):
        _seed_focus(app, 30)
        app.sample_snapshot()               # data changed since mount → draws
        drawn = calls["n"]
        assert drawn >= 1
        app.sample_snapshot()               # identical fingerprint → skipped
        app.sample_snapshot()
        assert calls["n"] == drawn


@pytest.mark.asyncio
async def test_new_bar_and_last_price_changes_redraw(tmp_path, monkeypatch):
    app = _app(tmp_path)
    calls = _count_replots(monkeypatch)
    async with app.run_test(size=(120, 60)):
        _seed_focus(app, 30)
        app.sample_snapshot()
        n1 = calls["n"]
        # Tick inside the CURRENT bar: bar count holds, last close changes.
        app._focus_candles.add(_BASE + 29 * app._tf.bar_ns + 1, 250.0, 1.0)
        app.sample_snapshot()
        n2 = calls["n"]
        assert n2 > n1
        # New bar bucket: bar count changes.
        app._focus_candles.add(_BASE + 30 * app._tf.bar_ns, 251.0, 1.0)
        app.sample_snapshot()
        assert calls["n"] > n2


@pytest.mark.asyncio
async def test_theme_change_redraws(tmp_path, monkeypatch):
    app = _app(tmp_path)
    calls = _count_replots(monkeypatch)
    async with app.run_test(size=(120, 60)):
        _seed_focus(app, 30)
        app.sample_snapshot()
        n1 = calls["n"]
        app.sample_snapshot()
        assert calls["n"] == n1             # settled
        app.theme = "dracula"
        app.sample_snapshot()
        assert calls["n"] > n1


@pytest.mark.asyncio
async def test_chart_type_and_volume_toggle_redraw(tmp_path, monkeypatch):
    app = _app(tmp_path)
    calls = _count_replots(monkeypatch)
    async with app.run_test(size=(120, 60)):
        _seed_focus(app, 30)
        app.sample_snapshot()
        n1 = calls["n"]
        app.cfg = msgspec.structs.replace(app.cfg, chart_type="line")
        app.sample_snapshot()
        n2 = calls["n"]
        assert n2 > n1
        app.cfg = msgspec.structs.replace(app.cfg, show_volume=False)
        app.sample_snapshot()
        assert calls["n"] > n2


@pytest.mark.asyncio
async def test_focus_symbol_key_change_redraws(tmp_path, monkeypatch):
    app = _app(tmp_path)
    calls = _count_replots(monkeypatch)
    async with app.run_test(size=(120, 60)):
        _seed_focus(app, 30)
        app.sample_snapshot()
        n1 = calls["n"]
        # set_reactive: change the memo key without firing the focus watcher
        # (which would swap aggregators and kick a warmup worker).
        app.set_reactive(EntropyApp.focus_symbol, "FAKE")
        app.sample_snapshot()
        assert calls["n"] > n1


# ------------------------------------------------------------ EMA overlays


@pytest.mark.asyncio
async def test_overlays_reach_chart_when_strategy_is_warm(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)):
        cfg = app._focus_strategy_cfg()
        assert cfg is app.crypto_strategy.cfg  # focus defaults to the crypto symbol
        _seed_focus(app, 30)                   # 30 closes ≥ slow period (21)
        app.sample_snapshot()
        chart = app.query_one("#price", PriceChart)
        assert chart.overlays is not None
        assert set(chart.overlays) == {f"EMA{cfg.fast}", f"EMA{cfg.slow}"}
        # Right-alignment: the last overlay value lands on the last candle.
        aligned = align_overlay(len(chart.candles), chart.overlays[f"EMA{cfg.slow}"])
        assert len(aligned) == len(chart.candles) and aligned[-1] is not None
        # Titles survive the new draw path.
        assert chart.title == f"{app.focus_symbol} · {app._tf.name}"
        assert (
            app.query_one("#price2", PriceChart).title
            == f"{app.cfg.strategy_symbol} · {app._tf.name}"
        )


@pytest.mark.asyncio
async def test_overlays_skipped_below_warmup(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)):
        cfg = app._focus_strategy_cfg()
        _seed_focus(app, cfg.fast - 1)          # not enough closes for either EMA
        app.sample_snapshot()
        chart = app.query_one("#price", PriceChart)
        assert chart.overlays is None
        assert len(chart.candles) == cfg.fast - 1


@pytest.mark.asyncio
async def test_only_fast_overlay_between_periods(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)):
        cfg = app._focus_strategy_cfg()
        n = cfg.fast + 2                        # fast warm, slow (21) not
        assert n < cfg.slow
        _seed_focus(app, n)
        app.sample_snapshot()
        chart = app.query_one("#price", PriceChart)
        assert chart.overlays is not None
        assert set(chart.overlays) == {f"EMA{cfg.fast}"}


@pytest.mark.asyncio
async def test_volume_up_down_flags_flow_from_bars(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)):
        _seed_focus(app, 10)                    # single-trade bars: o == c → up
        # Second tick in the last bar below its open → down bar.
        app._focus_candles.add(_BASE + 9 * app._tf.bar_ns + 1, 1.0, 2.0)
        app.sample_snapshot()
        volume = app.query_one("#volume", VolumeChart)
        assert volume.ups == [True] * 9 + [False]
