"""Tabbed settings modal: chart-interval decoupling, bot validation, the
watchlist manager and the settings-file round trip."""

from __future__ import annotations

import pytest
from textual.widgets import Button, Input, Select, Static, Switch, TabbedContent

from entropy import settings as settings_store
from entropy.app import AppConfig
from entropy.bot.config import STRATEGY_NAMES, BotConfig
from entropy.data.watchlist import Watchlist
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.charts import PriceChart, VolumeChart
from entropy.ui.widgets.modals import ErrorScreen, SettingsScreen

_1M_NS = 60 * 1_000_000_000
_15M_NS = 15 * _1M_NS


def _app(tmp_path, **kw) -> EntropyApp:
    return EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False,
        watchlist_path=str(tmp_path / "watchlist.json"),
        trade_csv_path=str(tmp_path / "trades.csv"),
        **kw,
    ))


def _static_text(widget: Static) -> str:
    return str(widget.content)


async def _open(pilot, app, key: str = "s") -> SettingsScreen:
    await pilot.press(key)
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, SettingsScreen)
    return screen


# --- structure -------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_tab_is_present(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app)
        tabs = screen.query_one("#settings-tabs", TabbedContent)
        ids = [pane.id for pane in tabs.query("TabPane")]
        assert ids == [
            "tab-general", "tab-chart", "tab-scanner",
            "tab-feeds", "tab-bot", "tab-watchlist",
        ]
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_b_opens_bot_tab_and_ctrl_w_opens_watchlist_tab(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="b")
        assert screen.query_one("#settings-tabs", TabbedContent).active == "tab-bot"
        await pilot.press("escape")
        await pilot.pause()

        screen = await _open(pilot, app, key="ctrl+w")
        assert screen.query_one("#settings-tabs", TabbedContent).active == "tab-watchlist"
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_status_bar_names_the_focus_symbol(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        app.sample_snapshot()
        rendered = app.query_one("#status").render().plain
        assert f"FOCUS {app.focus_symbol}" in rendered
        assert "/ search" in rendered
        await pilot.press("q")


# --- chart interval --------------------------------------------------------


@pytest.mark.asyncio
async def test_chart_interval_change_leaves_the_engine_alone(tmp_path):
    """The whole point of the split: candle width moves, the scanner does not."""
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        engine_before = app.engine
        strategy_before = app.strategy
        assert app._chart_bar_ns == _15M_NS

        screen = await _open(pilot, app)
        screen.query_one("#set-chart-interval", Select).value = "1m"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.cfg.chart_interval == "1m"
        assert app._chart_interval_name == "1m"
        assert app._chart_bar_ns == _1M_NS
        assert app._price_candles.interval_ns == _1M_NS
        assert app._focus_candles.interval_ns == _1M_NS
        assert app.query_one("#price", PriceChart).bar_ns == _1M_NS
        assert app.query_one("#volume", VolumeChart).bar_ns == _1M_NS

        # Engine/timeframe untouched: same object, same window labels, same tf.
        assert app.engine is engine_before
        assert app.strategy is strategy_before
        assert app._tf.name == "15m"
        assert app.cfg.timeframe == "15m"
        assert app.engine.cfg.window_labels == ("15m", "1h", "4h")
        assert app.query_one("#hist").window_labels == ("15m", "1h", "4h")
        await pilot.press("q")


@pytest.mark.asyncio
async def test_chart_titles_spell_out_a_decoupled_interval(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.query_one("#price", PriceChart).title.endswith("· 15m")

        screen = await _open(pilot, app)
        screen.query_one("#set-chart-interval", Select).value = "1m"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.query_one("#price", PriceChart).title == (
            f"{app.focus_symbol} · 1m candles · 15m scan"
        )
        assert app.query_one("#price2", PriceChart).title == (
            f"{app.cfg.strategy_symbol} · 1m candles · 15m scan"
        )
        await pilot.press("q")


@pytest.mark.asyncio
async def test_explicit_chart_interval_survives_a_timeframe_change(tmp_path):
    app = _app(tmp_path, chart_interval="1m")
    async with app.run_test(size=(120, 60)) as pilot:
        assert app._chart_bar_ns == _1M_NS

        screen = await _open(pilot, app)
        screen.query_one("#set-timeframe", Select).value = "1h"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app._tf.name == "1h"
        assert app.engine.cfg.window_labels == ("1h", "4h", "1d")
        assert app._chart_bar_ns == _1M_NS          # candles stayed where asked
        assert app._price_candles.interval_ns == _1M_NS
        await pilot.press("q")


@pytest.mark.asyncio
async def test_focus_warmup_fetches_the_chart_interval(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_klines(symbol, interval="1m", limit=200):
        calls.append((symbol, interval))
        return []

    monkeypatch.setattr("entropy.ui.app.warmup_klines", fake_klines)
    app = _app(tmp_path, chart_interval="1m")
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        app.focus_symbol = "binance-spot:ETHUSDT"
        for _ in range(20):
            await pilot.pause()
            if calls:
                break
        # Chart history follows the CANDLE interval, not the 15m scan timeframe.
        assert calls == [("ETHUSDT", "1m")]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_sub_minute_equity_chart_skips_the_fetch(tmp_path, monkeypatch):
    """No provider serves 5s equity bars: warm nothing and say so, rather than
    firing a request under a bogus interval name."""
    calls: list[tuple[str, str]] = []

    async def fake_equity(symbol, interval="15m", limit=64):
        calls.append((symbol, interval))
        return []

    monkeypatch.setattr("entropy.ui.app.warmup_equity_bars", fake_equity)
    app = _app(tmp_path, chart_interval="5s")
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        app._equity_source_resolved = "live"
        app.focus_symbol = "AAPL"
        for _ in range(20):
            await pilot.pause()
        assert calls == []
        console = app.query_one("#console")
        text = "\n".join(strip.text for strip in console.lines)
        assert "no 5s history for [AAPL]" in text
        await pilot.press("q")


@pytest.mark.asyncio
async def test_chart_bars_sets_the_aggregator_ring_size(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app)
        screen.query_one("#set-chart-bars", Input).value = "40"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.cfg.chart_bars == 40
        assert app._focus_candles._bars.maxlen == 40
        assert app._price_candles._bars.maxlen == 40
        await pilot.press("q")


@pytest.mark.asyncio
async def test_depth_switch_toggles_the_ladder(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.query_one("#depth").display is False
        screen = await _open(pilot, app)
        screen.query_one("#set-depth", Switch).value = True
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()
        assert app.cfg.show_depth is True
        assert app.query_one("#depth").display is True
        await pilot.press("q")


# --- bot tab ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_settings_apply_and_reach_the_app(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.bot_cfg.timeframe == "1m"

        screen = await _open(pilot, app, key="b")
        screen.query_one("#bot-timeframe", Select).value = "5m"
        screen.query_one("#bot-bar-s", Input).value = "0"
        screen.query_one("#bot-cash", Input).value = "50000"
        screen.query_one("#bot-strat-momentum_scalper", Switch).value = True
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.screen.id != "settings"
        assert app.bot_cfg.timeframe == "5m"
        assert app.bot_cfg.bar_seconds() == 300.0
        assert app.bot_cfg.starting_cash == 50_000.0
        assert "momentum_scalper" in app.bot_cfg.strategies
        await pilot.press("q")


@pytest.mark.asyncio
async def test_bar_override_hint_tracks_the_fields(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="b")
        hint = screen.query_one("#bot-bar-effective", Static)
        assert "60s" in _static_text(hint)  # 1m default timeframe

        screen.query_one("#bot-timeframe", Select).value = "15m"
        await pilot.pause()
        assert "900s" in _static_text(hint)

        screen.query_one("#bot-bar-s", Input).value = "12.5"
        await pilot.pause()
        assert "12.5s" in _static_text(hint) and "override" in _static_text(hint)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_risk_description_follows_the_select(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="b")
        desc = screen.query_one("#bot-risk-desc", Static)
        assert "Medium:" in _static_text(desc)
        screen.query_one("#bot-risk", Select).value = "extreme"
        await pilot.pause()
        assert "Extreme:" in _static_text(desc)
        await pilot.press("escape")


@pytest.mark.parametrize(
    "setup,fragment",
    [
        ("ema_order", "shorter than slow"),
        ("no_strategy", "at least one strategy"),
        ("live_unacknowledged", "risk acknowledgement"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_bot_settings_are_rejected_and_the_modal_stays_open(
    tmp_path, setup, fragment
):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        before = app.bot_cfg
        screen = await _open(pilot, app, key="b")
        if setup == "ema_order":
            screen.query_one("#bot-ind-ema-fast", Input).value = "50"
        elif setup == "no_strategy":
            for name in STRATEGY_NAMES:
                screen.query_one(f"#bot-strat-{name}", Switch).value = False
        else:
            screen.query_one("#bot-mode", Select).value = "live"
            screen.query_one("#bot-live-ack", Switch).value = False
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert isinstance(app.screen, ErrorScreen)
        assert fragment in app.screen._text
        # Nothing applied, and the form is still there behind the error.
        assert app.bot_cfg is before
        assert any(isinstance(s, SettingsScreen) for s in app.screen_stack)
        assert screen._saving is False

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen
        await pilot.click("#btn-cancel")


@pytest.mark.asyncio
async def test_live_mode_applies_once_acknowledged(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="b")
        screen.query_one("#bot-mode", Select).value = "live"
        screen.query_one("#bot-live-ack", Switch).value = True
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        assert app.screen.id != "settings"
        assert app.bot_cfg.mode == "live"
        assert app.bot_cfg.live.enabled is True
        assert app.bot_cfg.live.acknowledged_risk is True
        await pilot.press("q")


@pytest.mark.asyncio
async def test_non_numeric_bot_field_is_rejected(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="b")
        screen.query_one("#bot-cash", Input).value = "nan"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()
        assert isinstance(app.screen, ErrorScreen)
        assert "finite" in app.screen._text
        assert app.bot_cfg.starting_cash == 100_000.0
        await pilot.press("escape")
        await pilot.click("#btn-cancel")


@pytest.mark.asyncio
async def test_reset_button_restores_bot_defaults(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="b")
        screen.query_one("#bot-cash", Input).value = "1"
        screen.query_one("#bot-risk", Select).value = "extreme"
        await pilot.pause()
        # The Bot pane scrolls: the Reset button sits below the fold until it does.
        screen.query_one("#tab-bot").scroll_end(animate=False)
        await pilot.pause()
        await pilot.click("#btn-bot-reset")
        await pilot.pause()

        defaults = BotConfig()
        assert screen.query_one("#bot-cash", Input).value == str(defaults.starting_cash)
        assert screen.query_one("#bot-risk", Select).value == defaults.risk_profile
        await pilot.press("escape")


# --- watchlist tab ---------------------------------------------------------


@pytest.mark.asyncio
async def test_watchlist_add_and_remove_persist(tmp_path):
    path = tmp_path / "watchlist.json"
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="ctrl+w")
        screen.query_one("#watch-input", Input).value = "aapl"
        await pilot.pause()
        await pilot.click("#btn-watch-add")
        await pilot.pause()

        assert "AAPL" in app._watchlist
        assert [i.symbol for i in Watchlist(path).items()] == ["AAPL"]
        assert screen.query_one("#watch-table").row_count == 1
        assert screen.query_one("#watch-input", Input).value == ""

        await pilot.click("#btn-watch-remove")
        await pilot.pause()

        assert "AAPL" not in app._watchlist
        assert Watchlist(path).items() == []
        assert screen.query_one("#watch-table").row_count == 0
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_watchlist_add_reports_duplicates_and_empty_input(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="ctrl+w")
        status = screen.query_one("#watch-status", Static)

        # Button.press() rather than pilot.click(): Textual swallows a second
        # click on the same button while its "-active" flash is still running,
        # which would make this wall-clock dependent.
        add = screen.query_one("#btn-watch-add", Button)

        add.press()                                     # nothing typed
        await pilot.pause()
        assert "Type a symbol" in _static_text(status)

        screen.query_one("#watch-input", Input).value = "MSFT"
        await pilot.pause()
        add.press()
        await pilot.pause()
        screen.query_one("#watch-input", Input).value = "MSFT"
        await pilot.pause()
        add.press()
        await pilot.pause()

        assert "already followed" in _static_text(status)
        assert [i.symbol for i in app._watchlist.items()] == ["MSFT"]
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_watchlist_table_selection_does_not_move_the_chart(tmp_path):
    """The modal's table bubbles the same RowSelected the boards do; picking a
    row there must not yank the chart out from under the user."""
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        screen = await _open(pilot, app, key="ctrl+w")
        screen.query_one("#watch-input", Input).value = "NVDA"
        await pilot.pause()
        await pilot.click("#btn-watch-add")
        await pilot.pause()

        focus_before = app.focus_symbol
        table = screen.query_one("#watch-table")
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.focus_symbol == focus_before
        await pilot.press("escape")


# --- persistence -----------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_round_trip_through_the_settings_file(settings_path):
    """Boot from a persisted file, change something, and see it on disk."""
    settings_store.save(
        settings_store.EntropySettings(
            app=AppConfig(
                enable_crypto=False, enable_equities=False,
                watchlist_path=str(settings_path.parent / "watchlist.json"),
                trade_csv_path=str(settings_path.parent / "trades.csv"),
                timeframe="1h", chart_interval="5m",
            ),
            bot=BotConfig(timeframe="15m", starting_cash=12_345.0),
        ),
        settings_path,
    )

    app = EntropyApp()  # no explicit config: the file is the source of truth
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.cfg.timeframe == "1h"
        assert app.cfg.chart_interval == "5m"
        assert app.bot_cfg.timeframe == "15m"
        assert app.bot_cfg.starting_cash == 12_345.0

        screen = await _open(pilot, app)
        screen.query_one("#set-chart-interval", Select).value = "1m"
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        stored = settings_store.load(settings_path)
        assert stored.app.chart_interval == "1m"
        assert stored.app.timeframe == "1h"
        assert stored.bot.starting_cash == 12_345.0   # the bot half survived
        await pilot.press("q")


@pytest.mark.asyncio
async def test_command_bar_timeframe_change_is_persisted(tmp_path, settings_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        app._execute_command("tf 5m")
        await pilot.pause()
        assert settings_store.load(settings_path).app.timeframe == "5m"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_settings_save_failure_is_reported_not_raised(tmp_path, monkeypatch):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()

        def boom(*_args, **_kw):
            raise OSError("read-only volume")

        monkeypatch.setattr(settings_store, "save_app", boom)
        app._execute_command("tf 5m")
        await pilot.pause()

        assert app.cfg.timeframe == "5m"          # applied in-session regardless
        assert "read-only volume" in app._error_text
        await pilot.press("q")
