import pytest
from entropy_sidecar.commands import apply_command

from entropy.settings import load


@pytest.fixture
def src(offline_source):
    return offline_source()


def test_chart_sets_focus(src):
    res = apply_command(src, "chart aapl")
    assert res.ok is True
    assert src.focus == "AAPL"


def test_depth_with_symbol_focuses_and_shows_the_ladder(src):
    assert apply_command(src, "depth tsla").ok is True
    assert src.focus == "TSLA"
    assert src.cfg.show_depth is True


def test_bare_depth_toggles(src):
    assert src.cfg.show_depth is False
    assert apply_command(src, "depth").message == "depth on"
    assert src.cfg.show_depth is True
    assert apply_command(src, "depth").message == "depth off"
    assert src.cfg.show_depth is False


def test_unknown_verb_reports_error(src):
    res = apply_command(src, "frobnicate x")
    assert res.ok is False and "unknown" in res.message.lower()


def test_tf_changes_the_engine_and_persists(src, sidecar_settings):
    before = src.engine.cfg.window_labels
    res = apply_command(src, "tf 1h")
    assert res.ok is True and res.message == "timeframe 1h"
    assert src.cfg.timeframe == "1h"
    assert src.engine.cfg.window_labels != before
    assert load(sidecar_settings).app.timeframe == "1h"


def test_tf_with_a_bad_timeframe_is_rejected(src):
    res = apply_command(src, "tf 7y")
    assert res.ok is False
    assert src.cfg.timeframe == "15m"


def test_theme_applies_a_known_theme_and_rejects_others(src):
    assert apply_command(src, "theme nord").ok is True
    assert src.cfg.theme == "nord"
    bad = apply_command(src, "theme neon")
    assert bad.ok is False and "unknown theme" in bad.message
    assert src.cfg.theme == "nord"


def test_source_changes_the_equity_source(src):
    assert apply_command(src, "source live").ok is True
    assert src.cfg.equity_source == "live"
    assert apply_command(src, "source magic").ok is False


def test_watch_and_unwatch_hit_the_persistent_watchlist(src):
    assert apply_command(src, "watch aapl").ok is True
    assert [i.symbol for i in src.watched()] == ["AAPL"]
    again = apply_command(src, "watch aapl")
    assert again.ok is False and "already" in again.message
    assert apply_command(src, "unwatch aapl").ok is True
    assert src.watched() == []
    assert apply_command(src, "unwatch aapl").ok is False


def test_help_returns_the_grammar(src):
    res = apply_command(src, "help")
    assert res.ok is True and "chart SYM" in res.message


def test_no_verb_ever_returns_a_bare_acknowledgement(src):
    """Regression: every verb used to fall through to ok=True "acknowledged"."""
    for text in ("tf 7y", "theme neon", "source magic", "frobnicate x",
                 "chart", "help extra"):
        res = apply_command(src, text)
        assert "acknowledged" not in res.message
        assert res.ok is False, text
