from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmRiskScreen(ModalScreen[None]):
    """Asks the user to confirm a risk-profile change. On confirm, invokes the callback."""

    def __init__(self, new_profile: str, on_confirm: Callable[[], None]) -> None:
        super().__init__()
        self._new = new_profile
        self._on_confirm = on_confirm

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(f"Change risk profile to {self._new.upper()}?")
            yield Button("Confirm", id="confirm", variant="warning")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._on_confirm()
        self.dismiss(None)
