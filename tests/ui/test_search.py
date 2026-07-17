import msgspec
import pytest
from textual.widgets import OptionList

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp
from entropy.ui.widgets.search import SearchScreen


def _app(tmp_path, **kw) -> EntropyApp:
    return EntropyApp(AppConfig(
        enable_crypto=False, enable_equities=False,
        watchlist_path=str(tmp_path / "watchlist.json"), **kw,
    ))


@pytest.mark.asyncio
async def test_search_flow_focuses_symbol_and_dismisses(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("slash")
        assert isinstance(app.screen, SearchScreen)
        await pilot.press(*"AAPL")
        options = app.screen.query_one("#search-results", OptionList)
        assert options.option_count > 0                      # results shown live
        assert options.get_option_at_index(0).id == "AAPL"   # exact match ranks first
        await pilot.press("enter")
        assert app.focus_symbol == "AAPL"                    # selection focused it
        assert not isinstance(app.screen, SearchScreen)      # and dismissed
        await pilot.press("q")


@pytest.mark.asyncio
async def test_search_escape_dismisses_without_focus_change(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        before = app.focus_symbol
        await pilot.press("slash")
        await pilot.press(*"NVDA")
        await pilot.press("escape")
        assert not isinstance(app.screen, SearchScreen)
        assert app.focus_symbol == before
        await pilot.press("q")


@pytest.mark.asyncio
async def test_ctrl_w_toggles_watchlist_and_persists(tmp_path):
    wl_path = tmp_path / "watchlist.json"
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("slash")
        options = app.screen.query_one("#search-results", OptionList)
        assert options.option_count > 0        # curated defaults on empty query
        first = options.get_option_at_index(0).id
        assert first is not None

        await pilot.press("ctrl+w")            # Input must NOT eat this as delete-word
        assert first in app._watchlist
        stored = msgspec.json.decode(wl_path.read_bytes())
        assert [e["symbol"] for e in stored] == [first]
        assert isinstance(app.screen, SearchScreen)          # stays open
        options = app.screen.query_one("#search-results", OptionList)
        prompt = options.get_option_at_index(0).prompt
        assert "★" in getattr(prompt, "plain", str(prompt))  # marker updated

        await pilot.press("ctrl+w")                          # toggle back off
        assert first not in app._watchlist
        assert msgspec.json.decode(wl_path.read_bytes()) == []
        prompt = options.get_option_at_index(0).prompt
        assert "★" not in getattr(prompt, "plain", str(prompt))
        await pilot.press("escape")
        await pilot.press("q")


@pytest.mark.asyncio
async def test_w_toggles_focus_symbol_watch(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.focus_symbol == "binance-spot:BTCUSDT"
        await pilot.press("w")
        assert "binance-spot:BTCUSDT" in app._watchlist
        item = app._watchlist.items()[0]
        assert item.asset_class == "crypto"     # resolved via universe exact match
        await pilot.press("w")
        assert "binance-spot:BTCUSDT" not in app._watchlist
        await pilot.press("q")


def test_help_lists_new_keys():
    from entropy.ui.widgets.modals import _HELP

    assert "/" in _HELP and "search" in _HELP.lower()
    assert "w  " in _HELP and "atch" in _HELP   # watch toggle documented
    assert "row" in _HELP.lower()               # row-click focus documented
