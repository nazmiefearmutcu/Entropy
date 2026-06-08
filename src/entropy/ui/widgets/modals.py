from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP = """Entropy — keys:
  s  Settings    ?/h  Help    e  Errors    q  Quit
Scanner: new highs/lows over 30s/1m/5m/20m/session; spikes & snap-drops.
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("h", "dismiss", "Close"),
        ("q", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(_HELP, id="help-body")

    async def action_dismiss(self, result: None = None) -> None:
        self.app.pop_screen()


class SettingsScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close"), ("s", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static("Settings (read-only in v1)", id="settings-body")

    async def action_dismiss(self, result: None = None) -> None:
        self.app.pop_screen()


class ErrorScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close"), ("e", "dismiss", "Close")]

    def __init__(
        self,
        text: str = "No errors.",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._text = text

    def compose(self) -> ComposeResult:
        yield Static(self._text, id="error-body")

    async def action_dismiss(self, result: None = None) -> None:
        self.app.pop_screen()
