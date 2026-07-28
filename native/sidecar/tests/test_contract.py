import msgspec
from entropy_sidecar.contract import (
    BotPosition,
    BotStrategy,
    BotView,
    CommandRequest,
    CommandResult,
    DepthLevels,
    FeedStatus,
    FocusView,
    SettingsPatch,
    SettingsView,
    SnapshotMessage,
)
from entropy_sidecar.stream import SCHEMA_VERSION


def _snapshot() -> SnapshotMessage:
    return SnapshotMessage(
        schema_version=SCHEMA_VERSION, ts_ns=1,
        buy_pct=55.0, sell_pct=45.0, raw_hz=640.0, accel="steady",
        new_highs=[("AAPL", 122, 228.6, 1.9)], new_lows=[("DKNG", 28, 39.2, -2.1)],
        ticker=[("15m", [("AAPL", 12)])],
        focus=FocusView(
            symbol="AAPL", asset="EQUITY", last=228.6, pct=0.9, hi=229.4, lo=226.1,
            candles=[(1, 228.0, 229.0, 227.5, 228.6, 1000.0)],
            depth=DepthLevels(basis="yahoo_1m_vap", is_synthetic=True,
                              reference_price=228.66, bids=[(228.6, 2400.0)],
                              asks=[(228.7, 168.0)]),
            fundamentals=None, interval="1m", timeframe="15m",
        ),
        watchlist=[("NVDA", "NVDA", "NVIDIA Corp", "US", 121.4, 2.1, [1.0, 2.0, 3.0])],
        market_status="open", source="live",
        settings=SettingsView(timeframe="15m", chart_interval="1m", theme="nord"),
        feeds=FeedStatus(equities="live", crypto="connecting", detail="alpaca"),
        bot=BotView(
            running=True, paused=False, halted=False, warm=True, mode="paper",
            timeframe="1m", bar_s=60.0, risk_profile="Medium", risk_description="...",
            ticks=42, cash=1.0, equity=2.0, realized_pnl=0.5, unrealized_pnl=-0.25,
            daily_pnl=0.25, open_count=1,
            positions=[BotPosition(symbol="SPY", side="long", qty=1.0, entry_px=1.0,
                                   mark_px=1.1, unrealized_pnl=0.1, stop_px=0.9,
                                   tp_px=1.2)],
            strategies=[BotStrategy(name="consensus", warm=True,
                                    regimes={"SPY": "trend"}, directions={"SPY": 1})],
            last_signals=["consensus enter SPY"], last_rejects=["SPY: cooldown"],
        ),
    )


def test_snapshot_roundtrips_json():
    msg = _snapshot()
    back = msgspec.json.decode(msgspec.json.encode(msg), type=SnapshotMessage)
    assert back == msg
    assert back.focus.depth.is_synthetic is True
    assert back.schema_version == 2


def test_snapshot_carries_the_v2_panes():
    raw = msgspec.json.decode(msgspec.json.encode(_snapshot()))
    assert raw["type"] == "snapshot"
    assert raw["focus"]["interval"] == "1m" and raw["focus"]["timeframe"] == "15m"
    assert raw["settings"]["theme"] == "nord"
    assert raw["feeds"] == {"equities": "live", "crypto": "connecting", "detail": "alpaca"}
    assert raw["bot"]["running"] is True
    assert raw["watchlist"][0][0] == "NVDA"


def test_bot_is_null_before_the_bot_ever_starts():
    msg = msgspec.structs.replace(_snapshot(), bot=None)
    assert msgspec.json.decode(msgspec.json.encode(msg))["bot"] is None


def test_command_request_decodes():
    req = msgspec.json.decode(b'{"verb":"chart","arg":"AAPL"}', type=CommandRequest)
    assert (req.verb, req.arg) == ("chart", "AAPL")


def test_command_result_carries_problems():
    res = CommandResult(ok=False, message="nope", problems=["bad timeframe"])
    assert msgspec.json.decode(msgspec.json.encode(res))["problems"] == ["bad timeframe"]


def test_settings_patch_halves_are_optional():
    patch = msgspec.json.decode(b'{"app":{"theme":"nord"}}', type=SettingsPatch)
    assert patch.app == {"theme": "nord"}
    assert patch.bot is None
