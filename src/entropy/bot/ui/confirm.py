from __future__ import annotations

from typing import Any, Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, Static


class ConfirmRiskScreen(ModalScreen[bool]):
    """Asks the user to confirm a risk-profile change. On confirm, dismisses with True."""
    BINDINGS = [("escape", "dismiss_cancel", "Cancel")]

    def __init__(self, new_profile: str, on_confirm: Callable[[], None], on_cancel: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._new = new_profile
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(f"Are you sure with that '{self._new}' risk management mode?")
            yield Button("Confirm", id="confirm", variant="warning")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._on_confirm()
            self.dismiss(True)
        elif event.button.id == "cancel":
            if self._on_cancel is not None:
                self._on_cancel()
            self.dismiss(False)

    def action_dismiss_cancel(self) -> None:
        if self._on_cancel is not None:
            self._on_cancel()
        self.dismiss(False)


class BotSettingsScreen(ModalScreen[None]):
    """Settings modal for the Bot Dashboard."""
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saving = False

    def compose(self) -> ComposeResult:
        options = [
            ("Frosty", "frosty"),
            ("Medium", "medium"),
            ("Extreme", "extreme")
        ]
        current_profile = self.app.runner.risk.profile.name.lower() # type: ignore
        
        with Vertical(id="settings-box"):
            yield Label("Settings")
            yield Label("Risk Management Mode")
            yield Select(options=options, value=current_profile, allow_blank=False)
            with Horizontal():
                yield Button("Save", id="btn-save")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("btn-cancel", "cancel"):
            self.dismiss(None)
        elif event.button.id in ("btn-save", "save"):
            if self._saving:
                return
            self._saving = True
            try:
                selected_profile = self.query_one(Select).value
                if selected_profile is None:
                    self._saving = False
                    self.dismiss(None)
                    return
                
                selected_profile = str(selected_profile)
                current_profile = self.app.runner.risk.profile.name.lower() # type: ignore
                
                if selected_profile != current_profile:
                    def on_confirm() -> None:
                        self.app.apply_risk_change(selected_profile) # type: ignore
                        self.dismiss(None)
                    
                    def on_cancel() -> None:
                        self._saving = False
                    
                    self.app.push_screen(ConfirmRiskScreen(selected_profile.capitalize(), on_confirm, on_cancel))
                else:
                    self._saving = False
                    self.dismiss(None)
            except Exception:
                self._saving = False
                raise


