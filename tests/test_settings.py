"""Persisted settings: the file every frontend agrees on.

Reading must never be the reason the app refuses to start, so the load path is
tested against every way the file can be wrong.
"""

from __future__ import annotations

import msgspec
import pytest

from entropy import settings
from entropy.app import AppConfig
from entropy.bot.config import BotConfig, ConsensusConfig


@pytest.fixture
def path(tmp_path):
    return tmp_path / "settings.json"


def test_missing_file_is_a_first_run_not_an_error(path):
    loaded = settings.load(path)
    assert loaded == settings.EntropySettings()
    assert not path.exists()  # loading must not create anything


def test_round_trip(path):
    original = settings.EntropySettings(
        app=AppConfig(timeframe="1h", chart_interval="1m", theme="nord"),
        bot=BotConfig(timeframe="5m", consensus=ConsensusConfig(vote_mode="trend")),
    )
    settings.save(original, path)
    loaded = settings.load(path)
    assert loaded.app.timeframe == "1h"
    assert loaded.app.chart_interval == "1m"
    assert loaded.app.theme == "nord"
    assert loaded.bot.timeframe == "5m"
    assert loaded.bot.consensus.vote_mode == "trend"


def test_save_app_preserves_the_bot_half(path):
    settings.save(settings.EntropySettings(bot=BotConfig(timeframe="4h")), path)
    settings.save_app(AppConfig(theme="forest"), path)
    loaded = settings.load(path)
    assert loaded.app.theme == "forest"
    assert loaded.bot.timeframe == "4h"


def test_save_bot_preserves_the_app_half(path):
    settings.save(settings.EntropySettings(app=AppConfig(theme="dracula")), path)
    settings.save_bot(BotConfig(starting_cash=250_000.0), path)
    loaded = settings.load(path)
    assert loaded.app.theme == "dracula"
    assert loaded.bot.starting_cash == 250_000.0


@pytest.mark.parametrize("payload", [
    b"{ not json at all",
    b"[]",                                   # right JSON, wrong shape
    b'{"app": 5}',                           # right shape, wrong types
    b'{"app": {"timeframe": 99}}',           # nested type error
    b"",
])
def test_unreadable_file_falls_back_to_defaults(path, payload):
    path.write_bytes(payload)
    assert settings.load(path) == settings.EntropySettings()


def test_unknown_fields_are_ignored_for_forward_compatibility(path):
    """A newer build's file must not brick an older one."""
    path.write_bytes(b'{"version": 99, "app": {"theme": "nord", "from_the_future": true}}')
    loaded = settings.load(path)
    assert loaded.app.theme == "nord"


def test_missing_fields_take_defaults(path):
    path.write_bytes(b'{"app": {"theme": "nord"}}')
    loaded = settings.load(path)
    assert loaded.app.theme == "nord"
    assert loaded.app.timeframe == AppConfig().timeframe
    assert loaded.bot == BotConfig()


def test_save_is_atomic_and_leaves_no_temp_file(path):
    settings.save(settings.EntropySettings(), path)
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()


def test_save_failure_raises_and_cleans_up(tmp_path):
    """A genuine disk problem is reported, not swallowed — the UIs show it."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    with pytest.raises(OSError):
        settings.save(settings.EntropySettings(), blocked / "nested" / "settings.json")


def test_env_var_overrides_the_default_path(monkeypatch, tmp_path):
    target = tmp_path / "elsewhere.json"
    monkeypatch.setenv("ENTROPY_SETTINGS", str(target))
    assert settings.default_path() == target
    settings.save(settings.EntropySettings(app=AppConfig(theme="sweet")))
    assert target.exists()
    assert settings.load().app.theme == "sweet"


def test_every_config_field_survives_a_round_trip(path):
    """Guards against a field being added to AppConfig/BotConfig but excluded
    from persistence — the failure mode would be a setting that silently
    forgets itself between sessions."""
    original = settings.EntropySettings()
    settings.save(original, path)
    reloaded = settings.load(path)
    assert msgspec.json.encode(reloaded) == msgspec.json.encode(original)
