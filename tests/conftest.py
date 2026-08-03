import pytest


@pytest.fixture
def ns():
    """Helper to build nanosecond timestamps from float seconds."""
    return lambda s: int(s * 1_000_000_000)


@pytest.fixture(autouse=True)
def _deterministic_equity_source(monkeypatch):
    """Pin the app's "auto" equity-source resolution to "sim" for every test.

    The real resolver consults the wall clock, so any test that boots
    EntropyApp with the default config would hit the network-backed live feed
    whenever the suite happens to run during NYSE hours. Explicit "sim"/"live"
    values still pass through, and the resolver's own unit tests import it
    straight from entropy.feeds.equities.source (unpatched)."""
    from entropy.feeds.equities.source import resolve_equity_source as real_resolve

    def resolve(cfg_value: str, **kwargs):
        return "sim" if cfg_value == "auto" else real_resolve(cfg_value, **kwargs)

    monkeypatch.setattr("entropy.ui.app.resolve_equity_source", resolve)


@pytest.fixture(autouse=True)
def _isolate_runtime_artifacts(tmp_path, monkeypatch):
    """Keep every test's bot/UI artifact writes out of the repo root.

    BotConfig and AppConfig defaults point at the CWD (entropy_trades.csv /
    entropy_console.log); without this the suite appends to the repo's own
    files. Patching the class defaults makes fresh instances pick up tmp
    paths (msgspec reads defaults from the class attributes).
    """
    from entropy.app import AppConfig
    from entropy.bot.config import BotConfig

    for cls in (BotConfig, AppConfig):
        monkeypatch.setattr(
            cls, "console_log_path", str(tmp_path / "entropy_console.log")
        )
        monkeypatch.setattr(
            cls, "trade_csv_path", str(tmp_path / "entropy_trades.csv")
        )
