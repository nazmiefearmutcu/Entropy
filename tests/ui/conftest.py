from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def settings_path(tmp_path, monkeypatch) -> Path:
    """Point every UI test at a scratch settings file, and hand back its path.

    EntropyApp now loads persisted settings at construction and writes them back
    on every hot-apply, so without this the suite would read — and overwrite —
    the developer's real ~/.entropy/settings.json.
    """
    path = tmp_path / "settings.json"
    monkeypatch.setenv("ENTROPY_SETTINGS", str(path))
    return path
