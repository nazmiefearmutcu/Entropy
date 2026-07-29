"""Hermetic defaults for every sidecar test.

SnapshotSource reads persisted settings at construction, so without this the
suite would run against the developer's real ~/.entropy (and could start a LIVE
crypto WS or equity feed). The autouse fixture redirects ENTROPY_SETTINGS at a
per-test file whose config is deliberately offline: sim equities, no crypto,
watchlist inside tmp.
"""

from __future__ import annotations

import msgspec
import pytest

from entropy.app import AppConfig
from entropy.bot.config import BotConfig
from entropy.settings import EntropySettings


def write_settings(path, app: AppConfig, bot: BotConfig | None = None) -> None:
    path.write_bytes(
        msgspec.json.encode(EntropySettings(app=app, bot=bot or BotConfig()))
    )


@pytest.fixture(autouse=True)
def sidecar_settings(tmp_path, monkeypatch):
    """Point the settings store at a scratch file and return its path."""
    settings_path = tmp_path / "settings.json"
    cfg = AppConfig(
        equity_source="sim",
        enable_crypto=False,
        watchlist_path=str(tmp_path / "watchlist.json"),
    )
    write_settings(settings_path, cfg)
    monkeypatch.setenv("ENTROPY_SETTINGS", str(settings_path))
    return settings_path


@pytest.fixture
def offline_source(tmp_path):
    """Factory for a SnapshotSource with every network fetcher stubbed out.

    ``cfg_source`` / ``enable_crypto`` are shorthands for the two AppConfig
    fields that decide which feed a test exercises; anything else goes straight
    through to the constructor.
    """
    from entropy_sidecar.stream import SnapshotSource

    from entropy.settings import load

    async def _no_depth(_symbol):
        return None

    async def _no_fundamentals(_symbol):
        return None

    async def _no_bars(_symbol, _interval):
        return []

    def _make(*, cfg_source=None, enable_crypto=None, **kwargs):
        if cfg_source is not None or enable_crypto is not None:
            base = kwargs.get("cfg") or load().app
            kwargs["cfg"] = msgspec.structs.replace(
                base,
                equity_source=cfg_source if cfg_source is not None else base.equity_source,
                enable_crypto=(
                    enable_crypto if enable_crypto is not None else base.enable_crypto
                ),
            )
        kwargs.setdefault("depth_fetcher", _no_depth)
        kwargs.setdefault("fundamentals_fetcher", _no_fundamentals)
        kwargs.setdefault("klines_fetcher", _no_bars)
        kwargs.setdefault("equity_bars_fetcher", _no_bars)
        kwargs.setdefault("market_status_fn", lambda: "closed")
        kwargs.setdefault("bot_run_dir", str(tmp_path / "run"))
        return SnapshotSource(**kwargs)

    return _make
