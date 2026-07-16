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
