"""Persisted user settings shared by every Entropy frontend.

Before this, the TUI held its config in memory (gone on quit), the native
sidecar hardcoded its own, and the bot took CLI flags — so the same product had
three disagreeing notions of "my settings" and none of them survived a restart.
One file, ``~/.entropy/settings.json``, is now the single source of truth:
change the timeframe in the GUI, reopen the TUI, and it is still there.

Reading is deliberately unfailable. A missing file is a first run, and a
corrupt or partially-understood one falls back to defaults rather than refusing
to start — settings must never be the reason the app will not open. Writes are
atomic (tmp + ``os.replace``), so a crash mid-save cannot leave a half-written
file behind.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import msgspec

from entropy.app import AppConfig
from entropy.bot.config import BotConfig

log = logging.getLogger(__name__)

#: Bumped when a field's *meaning* changes (not when one is added — msgspec
#: fills missing fields from defaults and ignores unknown ones, so additive
#: changes are already backward and forward compatible).
SCHEMA_VERSION = 1

_ENV_PATH = "ENTROPY_SETTINGS"


class EntropySettings(msgspec.Struct):
    version: int = SCHEMA_VERSION
    app: AppConfig = msgspec.field(default_factory=AppConfig)
    bot: BotConfig = msgspec.field(default_factory=BotConfig)


def default_path() -> Path:
    """Settings location: ``$ENTROPY_SETTINGS`` if set, else ~/.entropy/settings.json.

    The env override is what lets tests — and a second app instance — run against
    a scratch file instead of stomping the user's real settings.
    """
    override = os.environ.get(_ENV_PATH)
    if override:
        return Path(override)
    return Path.home() / ".entropy" / "settings.json"


def load(path: Path | None = None) -> EntropySettings:
    """Read settings, degrading to defaults on any problem."""
    target = path or default_path()
    try:
        raw = target.read_bytes()
    except OSError:
        return EntropySettings()  # first run: no file, no log, no complaint
    try:
        return msgspec.json.decode(raw, type=EntropySettings)
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        log.warning("settings at %s unreadable (%s); using defaults", target, exc)
        return EntropySettings()


def save(settings: EntropySettings, path: Path | None = None) -> Path:
    """Atomically persist ``settings``; returns the path written.

    Raises ``OSError`` on a genuine disk problem — callers surface that to the
    user rather than pretending the save worked.
    """
    target = path or default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(msgspec.json.encode(settings))
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return target


def save_app(app: AppConfig, path: Path | None = None) -> Path:
    """Persist the app half, preserving whatever bot settings are on disk."""
    target = path or default_path()
    current = load(target)
    current.app = app
    return save(current, target)


def save_bot(bot: BotConfig, path: Path | None = None) -> Path:
    """Persist the bot half, preserving whatever app settings are on disk."""
    target = path or default_path()
    current = load(target)
    current.bot = bot
    return save(current, target)
