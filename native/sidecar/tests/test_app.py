import pytest
from entropy_sidecar.app import create_app
from fastapi.testclient import TestClient

from entropy.bot.config import BotConfig


@pytest.fixture
def client(offline_source):
    """TestClient over an offline source. No `with` block, so the lifespan (and
    therefore the feeds) never runs — the tests drive the engine directly."""
    return TestClient(create_app(source=offline_source()))


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ws_live_streams_one_snapshot(offline_source):
    src = offline_source()
    src.engine.on_trade("AAPL", 100.0, 1.0, "buy", 0)
    src.set_focus("AAPL")
    client = TestClient(create_app(source=src, tick_hz=50))
    with client.websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "snapshot"
    assert msg["schema_version"] == 2
    assert msg["focus"]["symbol"] == "AAPL"
    assert msg["settings"]["timeframe"] == src.cfg.timeframe
    assert msg["feeds"]["equities"] == "off"


# --- meta ---------------------------------------------------------------------


def test_meta_lists_every_vocabulary(client):
    meta = client.get("/api/meta").json()
    for key in ("timeframes", "chart_intervals", "themes", "strategies",
                "risk_profiles", "vote_modes", "normalize_modes", "exit_modes",
                "equity_sources"):
        assert meta[key], f"{key} must not be empty"
    assert "15m" in meta["timeframes"]
    assert "1s" in meta["chart_intervals"]
    assert meta["equity_sources"] == ["sim", "live", "auto"]
    assert meta["settings_path"].endswith("settings.json")
    profile = meta["risk_profiles"][0]
    for field in ("name", "description", "per_trade_pct", "max_concurrent",
                  "stop_loss_pct", "take_profit_pct", "max_total_exposure_pct",
                  "max_daily_loss_pct", "cooldown_s", "min_volatility_pct",
                  "vol_window_s", "color"):
        assert field in profile


# --- settings -----------------------------------------------------------------


def test_settings_get_returns_both_halves(client):
    body = client.get("/api/settings").json()
    assert body["app"]["timeframe"] == "15m"
    assert body["bot"]["mode"] == "paper"
    assert "consensus" in body["bot"] and "threshold" in body["bot"]["consensus"]


def test_partial_put_preserves_untouched_fields(client):
    before = client.get("/api/settings").json()
    r = client.put("/api/settings", json={"app": {"theme": "nord"}})
    assert r.json()["ok"] is True
    after = client.get("/api/settings").json()
    assert after["app"]["theme"] == "nord"
    assert after["app"]["watchlist_path"] == before["app"]["watchlist_path"]
    assert after["app"]["timeframe"] == before["app"]["timeframe"]
    assert after["bot"] == before["bot"]


def test_partial_put_reaches_nested_bot_fields(client):
    before = client.get("/api/settings").json()
    r = client.put("/api/settings", json={"bot": {"consensus": {"threshold": 0.7}}})
    assert r.json()["ok"] is True
    after = client.get("/api/settings").json()
    assert after["bot"]["consensus"]["threshold"] == 0.7
    # the rest of the nested struct survived the merge
    assert after["bot"]["consensus"]["ema_fast"] == before["bot"]["consensus"]["ema_fast"]
    assert after["bot"]["strategies"] == before["bot"]["strategies"]


def test_settings_round_trip(client):
    r = client.put("/api/settings", json={
        "app": {"timeframe": "1h", "chart_interval": "5m", "chart_type": "line"},
        "bot": {"risk_profile": "extreme", "timeframe": "5m"},
    })
    assert r.json() == {"ok": True, "message": "settings saved", "problems": []}
    after = client.get("/api/settings").json()
    assert after["app"]["timeframe"] == "1h"
    assert after["app"]["chart_interval"] == "5m"
    assert after["bot"]["risk_profile"] == "extreme"


def test_invalid_bot_config_changes_nothing(client):
    before = client.get("/api/settings").json()
    r = client.put("/api/settings", json={
        "app": {"theme": "dracula"},
        "bot": {"strategies": [], "starting_cash": -5},
    })
    body = r.json()
    assert body["ok"] is False
    assert body["problems"]
    assert any("strategy" in p for p in body["problems"])
    after = client.get("/api/settings").json()
    assert after == before          # the valid app half was NOT applied either


def test_invalid_app_config_is_reported(client):
    before = client.get("/api/settings").json()
    body = client.put("/api/settings", json={"app": {"timeframe": "7y"}}).json()
    assert body["ok"] is False and any("timeframe" in p for p in body["problems"])
    assert client.get("/api/settings").json() == before


def test_unknown_field_is_rejected_not_silently_dropped(client):
    body = client.put("/api/settings", json={"app": {"nonesuch": 1}}).json()
    assert body["ok"] is False and body["problems"]


# --- symbols / watchlist -------------------------------------------------------


def test_symbol_search_marks_watched(client):
    assert client.post("/api/watchlist", json={"symbol": "AAPL"}).json()["ok"] is True
    rows = client.get("/api/symbols", params={"q": "AAPL", "limit": 5}).json()
    assert rows and rows[0]["symbol"] == "AAPL"
    assert rows[0]["watched"] is True
    assert rows[0]["asset_class"] == "equity"
    other = client.get("/api/symbols", params={"q": "MSFT", "limit": 5}).json()
    assert other[0]["watched"] is False


def test_watchlist_add_list_delete(client):
    assert client.get("/api/watchlist").json() == []
    assert client.post("/api/watchlist", json={"symbol": "AAPL"}).json()["ok"] is True
    assert [i["symbol"] for i in client.get("/api/watchlist").json()] == ["AAPL"]

    dup = client.post("/api/watchlist", json={"symbol": "AAPL"}).json()
    assert dup["ok"] is False and "already" in dup["message"]

    assert client.delete("/api/watchlist/AAPL").json()["ok"] is True
    assert client.get("/api/watchlist").json() == []
    missing = client.delete("/api/watchlist/AAPL").json()
    assert missing["ok"] is False and "not watched" in missing["message"]


def test_watchlist_handles_colon_symbols(client):
    assert client.post(
        "/api/watchlist", json={"symbol": "binance-spot:BTCUSDT"}
    ).json()["ok"] is True
    row = client.get("/api/watchlist").json()[0]
    assert row["symbol"] == "binance-spot:BTCUSDT"
    assert row["asset_class"] == "crypto"
    assert client.delete("/api/watchlist/binance-spot:BTCUSDT").json()["ok"] is True
    assert client.get("/api/watchlist").json() == []


def test_watchlist_delete_accepts_the_percent_encoded_colon(client):
    # The frontend sends encodeURIComponent(symbol) -> binance-spot%3ABTCUSDT.
    client.post("/api/watchlist", json={"symbol": "binance-spot:BTCUSDT"})
    r = client.delete("/api/watchlist/binance-spot%3ABTCUSDT")
    assert r.json()["ok"] is True
    assert client.get("/api/watchlist").json() == []


def test_focus_endpoint(client):
    assert client.post("/api/focus", json={"symbol": "msft"}).json() == {
        "ok": True, "message": "focus MSFT", "problems": [],
    }
    assert client.app.state.source.focus == "MSFT"


def test_focus_requires_a_symbol(client):
    assert client.post("/api/focus", json={"symbol": ""}).json()["ok"] is False


# --- command bar ---------------------------------------------------------------


def test_command_endpoint_sets_focus(client):
    r = client.post("/api/command", json={"verb": "chart", "arg": "MSFT"})
    assert r.json()["ok"] is True
    assert client.app.state.source.focus == "MSFT"


def test_command_tf_actually_changes_the_timeframe(client):
    assert client.post("/api/command", json={"verb": "tf", "arg": "1h"}).json()["ok"] is True
    assert client.get("/api/settings").json()["app"]["timeframe"] == "1h"


def test_unsupported_verb_is_not_acknowledged(client):
    body = client.post("/api/command", json={"verb": "frobnicate", "arg": "x"}).json()
    assert body["ok"] is False
    assert "unknown" in body["message"].lower()


def test_bad_command_body_is_reported(client):
    r = client.post("/api/command", content=b"not json")
    assert r.json()["ok"] is False


# --- bot -----------------------------------------------------------------------


def test_bot_transitions_reach_the_snapshot(offline_source, tmp_path):
    bot_cfg = BotConfig(warmup=False, trade_csv_path=str(tmp_path / "trades.csv"))
    src = offline_source(bot_cfg=bot_cfg)
    client = TestClient(create_app(source=src))

    def bot_view():
        with client.websocket_connect("/ws/live") as ws:
            return ws.receive_json()["bot"]

    assert bot_view() is None
    assert client.post("/api/bot/start").json()["ok"] is True
    view = bot_view()
    assert view["running"] is True and view["paused"] is False
    assert view["mode"] == "paper" and view["strategies"]

    assert client.post("/api/bot/pause").json()["ok"] is True
    assert bot_view()["paused"] is True
    assert client.post("/api/bot/pause").json()["ok"] is False   # already paused
    assert client.post("/api/bot/resume").json()["ok"] is True
    assert bot_view()["paused"] is False

    assert client.post("/api/bot/halt").json()["ok"] is True
    assert bot_view()["halted"] is True

    assert client.post("/api/bot/stop").json()["ok"] is True
    assert bot_view()["running"] is False


def test_bot_control_before_start_is_honest(client):
    for route in ("/api/bot/pause", "/api/bot/resume", "/api/bot/halt", "/api/bot/stop"):
        body = client.post(route).json()
        assert body["ok"] is False, route
        assert body["message"]


def test_symbol_search_rows_carry_the_display_split(client):
    """The picker needs ticker/exchange as separate fields — before, it had to
    render the canonical id and a name with the venue baked into it."""
    rows = client.get("/api/symbols", params={"q": "bitcoin", "limit": 5}).json()
    assert rows
    top = rows[0]
    assert top["symbol"] == "binance-spot:BTCUSDT"
    assert top["ticker"] == "BTCUSDT"
    assert top["name"] == "Bitcoin / TetherUS"
    assert top["exchange"] == "BINANCE"
    assert (top["base"], top["quote"]) == ("BTC", "USDT")
    assert top["watched"] is False


def test_symbol_search_accepts_the_long_coin_name_and_a_pair_query(client):
    by_abbrev = client.get("/api/symbols", params={"q": "eth"}).json()
    by_name = client.get("/api/symbols", params={"q": "ethereum"}).json()
    by_pair = client.get("/api/symbols", params={"q": "eth usd"}).json()
    assert by_abbrev[0]["symbol"] == "binance-spot:ETHUSDT"
    assert by_name[0]["symbol"] == "binance-spot:ETHUSDT"
    assert by_pair[0]["symbol"] == "coinbase:ETH-USD"
