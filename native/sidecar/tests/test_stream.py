import asyncio

import msgspec
import pytest
from entropy_sidecar.contract import SnapshotMessage
from entropy_sidecar.stream import validate_app

from entropy.app import AppConfig
from entropy.bot.config import BotConfig
from entropy.engine.timeframe import get_timeframe


@pytest.mark.asyncio
async def test_build_snapshot_from_seeded_engine(offline_source):
    src = offline_source()
    src.engine.on_trade("AAPL", 100.0, 1.0, "buy", 0)
    src.engine.on_trade("AAPL", 110.0, 1.0, "buy", 1_000_000_000)
    src.set_focus("AAPL")
    msg = await src.build()
    assert isinstance(msg, SnapshotMessage)
    assert msg.schema_version == 2
    assert msg.focus.symbol == "AAPL"
    assert msg.focus.last == 110.0
    assert any(row[0] == "AAPL" for row in msg.new_highs)


@pytest.mark.asyncio
async def test_snapshot_echoes_settings_and_feed_state(offline_source):
    src = offline_source()
    msg = await src.build()
    assert msg.settings.timeframe == src.cfg.timeframe
    assert msg.settings.theme == src.cfg.theme
    assert msg.focus.timeframe == src.cfg.timeframe
    assert msg.focus.interval == get_timeframe(src.cfg.timeframe).name  # "" follows the tf
    assert msg.feeds.equities == "off"      # feeds not started: say off, not sim
    assert msg.bot is None                  # never started


@pytest.mark.asyncio
async def test_feeds_populate_engine(offline_source):
    from entropy.feeds.equities.universe import UNIVERSE

    src = offline_source()
    await src.start_feeds()
    try:
        # let the sim feed emit + drain briefly
        for _ in range(30):
            await asyncio.sleep(0.02)
            if any(src.engine.quote(s) is not None for s in UNIVERSE):
                break
        # the feed -> drain -> engine.on_trade pipeline saw real trades
        assert any(src.engine.quote(s) is not None for s in UNIVERSE)
        assert src._feed_equities == "sim"
        assert src._feed_crypto == "off"    # disabled in config, never connected
        msg = await src.build()
        assert isinstance(msg, SnapshotMessage)
    finally:
        await src.stop_feeds()


@pytest.mark.asyncio
async def test_watchlist_rows_reach_the_snapshot(offline_source):
    src = offline_source()
    ok, _ = src.add_watch("AAPL")
    assert ok
    src.engine.on_trade("AAPL", 100.0, 1.0, "buy", 0)
    src.engine.on_trade("AAPL", 110.0, 1.0, "buy", 1_000_000_000)
    await src.build()
    msg = await src.build()
    symbol, ticker, name, exchange, last, _pct, spark = next(
        r for r in msg.watchlist if r[0] == "AAPL"
    )
    assert (symbol, ticker, exchange) == ("AAPL", "AAPL", "US")
    assert name                       # the instrument is spelled out, not blank
    assert last == 110.0
    assert len(spark) == 2            # one sparkline sample appended per build


@pytest.mark.asyncio
async def test_crypto_watchlist_row_leads_with_the_exchange_ticker(offline_source):
    """The pane used to render the canonical id, which clipped to "binance-s…"
    — nine characters of venue before any information."""
    src = offline_source()
    ok, _ = src.add_watch("binance-spot:BTCUSDT")
    assert ok
    msg = await src.build()
    row = next(r for r in msg.watchlist if r[0] == "binance-spot:BTCUSDT")
    assert row[1] == "BTCUSDT"
    assert row[2] == "Bitcoin / TetherUS"
    assert row[3] == "BINANCE"


@pytest.mark.asyncio
async def test_timeframe_change_rebuilds_the_engine(offline_source):
    src = offline_source()
    before_engine = src.engine
    before_labels = src.engine.cfg.window_labels
    problems, persist = src.patch_app(timeframe="1h")
    assert (problems, persist) == ([], "")
    assert src.engine is not before_engine
    assert src.engine.cfg.window_labels != before_labels
    assert src.engine.cfg.window_labels == get_timeframe("1h").window_labels
    msg = await src.build()
    assert msg.settings.timeframe == "1h"


@pytest.mark.asyncio
async def test_chart_interval_change_leaves_the_engine_alone(offline_source):
    src = offline_source()
    labels = src.engine.cfg.window_labels
    before_ns = src._focus_candles.interval_ns
    problems, persist = src.patch_app(chart_interval="1m")
    assert (problems, persist) == ([], "")
    assert src._focus_candles.interval_ns != before_ns
    assert src._focus_candles.interval_ns == 60 * 1_000_000_000
    assert src.engine.cfg.window_labels == labels   # scanner cadence untouched
    msg = await src.build()
    assert msg.focus.interval == "1m"
    assert msg.focus.timeframe == src.cfg.timeframe


def test_invalid_app_config_is_rejected_without_changing_anything(offline_source):
    src = offline_source()
    before = src.cfg
    problems, _ = src.apply_app(msgspec.structs.replace(src.cfg, timeframe="7y"))
    assert problems and "timeframe" in problems[0]
    assert src.cfg == before


def test_validate_app_names_each_bad_field():
    bad = AppConfig(timeframe="7y", chart_interval="3y", theme="neon",
                    chart_type="pie", equity_source="magic", chart_bars=1)
    problems = validate_app(bad)
    joined = " ".join(problems)
    for token in ("timeframe", "chart interval", "theme", "chart type",
                  "equity source", "2 bars"):
        assert token in joined


def test_persisted_settings_are_the_source_of_truth(sidecar_settings, offline_source):
    src = offline_source()
    assert src.patch_app(theme="nord") == ([], "")
    from entropy.settings import load
    assert load(sidecar_settings).app.theme == "nord"
    # a fresh source reads it back
    assert offline_source().cfg.theme == "nord"


@pytest.mark.asyncio
async def test_bot_lifecycle_shows_up_in_the_snapshot(offline_source, tmp_path):
    bot_cfg = BotConfig(
        warmup=False,                                  # no network in tests
        trade_csv_path=str(tmp_path / "trades.csv"),
        console_log_path=str(tmp_path / "console.log"),
    )
    src = offline_source(bot_cfg=bot_cfg)
    assert (await src.build()).bot is None

    ok, message, problems = await src.start_bot()
    assert (ok, problems) == (True, []) and "started" in message
    view = (await src.build()).bot
    assert view is not None and view.running is True and view.paused is False
    assert view.risk_profile == "Medium"

    assert src.set_bot_paused(True)[0] is True
    assert (await src.build()).bot.paused is True
    assert src.set_bot_paused(True)[0] is False        # already paused: honest no-op
    assert src.set_bot_paused(False)[0] is True
    assert (await src.build()).bot.paused is False

    assert src.stop_bot()[0] is True
    stopped = (await src.build()).bot
    assert stopped is not None and stopped.running is False
    assert src.stop_bot()[0] is False                  # not running: says so
    await src.stop_feeds()


@pytest.mark.asyncio
async def test_bot_start_reports_an_unusable_config(offline_source, tmp_path):
    bot_cfg = BotConfig(
        strategies=(), warmup=False,
        trade_csv_path=str(tmp_path / "trades.csv"),
    )
    src = offline_source(bot_cfg=bot_cfg)
    ok, _message, problems = await src.start_bot()
    assert ok is False and problems
    assert src.bot is None
    assert (await src.build()).bot is None


@pytest.mark.asyncio
async def test_bot_start_reports_an_unwritable_ledger(offline_source, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    bot_cfg = BotConfig(warmup=False, trade_csv_path=str(tmp_path / "trades.csv"))
    src = offline_source(bot_cfg=bot_cfg, bot_run_dir=str(blocker / "run"))
    ok, message, problems = await src.start_bot()
    assert ok is False and problems
    assert "ledger" in message
    assert src.bot is None


@pytest.mark.asyncio
async def test_drain_survives_a_bot_that_raises(offline_source, tmp_path):
    from crocodile.core.schema.enums import AssetClass
    from crocodile.core.schema.records import Side, Trade

    bot_cfg = BotConfig(warmup=False, trade_csv_path=str(tmp_path / "trades.csv"))
    src = offline_source(bot_cfg=bot_cfg)
    await src.start_bot()

    class _Boom:
        paused = False

        def on_trade(self, *_args):
            raise RuntimeError("strategy exploded")

        def snapshot(self):
            raise AssertionError("not needed")

    src.bot = _Boom()
    drain = asyncio.create_task(src._drain())
    await src._sink.put(Trade(
        source="sim", symbol="AAPL", symbol_raw="AAPL", local_ts=0,
        asset_class=AssetClass.EQUITY, source_ts=0,
        id="1", price=100.0, amount=1.0, side=Side.BUY,
    ))
    for _ in range(20):
        await asyncio.sleep(0.01)
        if src.engine.quote("AAPL") is not None:
            break
    drain.cancel()
    # the tape kept flowing and the failure is reported, not swallowed
    assert src.engine.quote("AAPL") is not None
    assert "strategy exploded" in src._feed_detail


# --- feed wiring (stubbed transports: no network) ------------------------------


class _Plan:
    provider_name = "alpaca"
    trimmed_symbols: list[str] = []


async def _forever() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_live_equity_feed_reports_live_and_its_provider(offline_source, monkeypatch):
    import entropy_sidecar.stream as stream

    async def fake_start(_sink, _symbols):
        return asyncio.create_task(_forever()), _Plan()

    monkeypatch.setattr(stream, "start_equity_feed", fake_start)
    src = offline_source(cfg_source="live")
    await src.start_feeds()
    try:
        await asyncio.sleep(0.05)
        assert src.source == "live"
        assert (await src.build()).feeds.equities == "live"
        assert "alpaca" in src._feed_detail
    finally:
        await src.stop_feeds()


@pytest.mark.asyncio
async def test_live_equity_failure_falls_back_to_sim_and_says_why(
    offline_source, monkeypatch
):
    import entropy_sidecar.stream as stream

    async def boom(_sink, _symbols):
        raise RuntimeError("no api key")

    monkeypatch.setattr(stream, "start_equity_feed", boom)
    src = offline_source(cfg_source="live")
    await src.start_feeds()
    try:
        await asyncio.sleep(0.05)
        feeds = (await src.build()).feeds
        assert feeds.equities in ("error", "sim")   # error, then the sim feed takes over
        assert "no api key" in feeds.detail
        assert src.source == "sim"                  # focus asset must not claim EQUITY
    finally:
        await src.stop_feeds()


@pytest.mark.asyncio
async def test_crypto_connect_failure_is_an_error_not_silence(offline_source, monkeypatch):
    import entropy_sidecar.stream as stream

    async def boom(_sink):
        raise RuntimeError("ws handshake failed")

    monkeypatch.setattr(stream, "start_crypto_feed", boom)
    src = offline_source(enable_crypto=True)
    await src.start_feeds()
    try:
        await asyncio.sleep(0.05)
        feeds = (await src.build()).feeds
        assert feeds.crypto == "error"
        assert "ws handshake failed" in feeds.detail
    finally:
        await src.stop_feeds()


@pytest.mark.asyncio
async def test_crypto_toggle_leaves_the_equity_feed_alone(offline_source, monkeypatch):
    import entropy_sidecar.stream as stream

    async def fake_crypto(_sink):
        return asyncio.create_task(_forever())

    monkeypatch.setattr(stream, "start_crypto_feed", fake_crypto)
    src = offline_source()
    await src.start_feeds()
    try:
        equities = src._tasks["equities"]
        assert src.patch_app(enable_crypto=True) == ([], "")
        await asyncio.sleep(0.05)
        assert (await src.build()).feeds.crypto == "live"
        assert src._tasks["equities"] is equities      # untouched by the crypto toggle
        assert src.patch_app(enable_crypto=False) == ([], "")
        assert "crypto" not in src._tasks
        assert src._tasks["equities"] is equities
    finally:
        await src.stop_feeds()


@pytest.mark.asyncio
async def test_focus_change_seeds_the_chart_from_history(offline_source):
    from entropy.strategy.engine import Bar

    minute = 60 * 1_000_000_000

    async def bars(_symbol, _interval):
        return [Bar(ts_ns=i * minute, close=100.0 + i, high=101.0 + i, low=99.0 + i)
                for i in range(5)]

    src = offline_source(equity_bars_fetcher=bars, cfg_source="live")
    src.source = "live"
    assert src.patch_app(chart_interval="1m") == ([], "")
    src.set_focus("AAPL")
    await asyncio.sleep(0)
    await src._tasks["chart_warmup"]
    candles = (await src.build()).focus.candles
    assert len(candles) == 5
    assert candles[-1][4] == 104.0                 # close of the last seeded bar
    assert candles[-1][2] == 105.0                 # its high


@pytest.mark.asyncio
async def test_chart_warmup_is_skipped_when_no_provider_serves_the_width(offline_source):
    calls: list[tuple[str, str]] = []

    async def bars(symbol, interval):
        calls.append((symbol, interval))
        return []

    src = offline_source(equity_bars_fetcher=bars, cfg_source="live")
    src.source = "live"
    # 5s equity candles have no history source; warming would 400 or lie.
    assert src.patch_app(chart_interval="5s") == ([], "")
    src.set_focus("AAPL")
    await asyncio.sleep(0)
    await src._tasks["chart_warmup"]
    assert calls == []
