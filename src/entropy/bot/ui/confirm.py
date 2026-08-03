from __future__ import annotations

from collections.abc import Callable
from typing import Any

import msgspec
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from ..config import BotConfig, validate, warnings
from .widgets import TradeLog


class ConfirmRiskScreen(ModalScreen[bool]):
    """Asks the user to confirm a risk-profile change. On confirm, dismisses with True."""
    BINDINGS = [("escape", "dismiss_cancel", "Cancel")]

    def __init__(
        self,
        new_profile: str,
        on_confirm: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
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
        current_profile = self.app.runner.risk.profile.name.lower()  # type: ignore
        cfg = self.app.runner.config  # type: ignore

        with Vertical(id="settings-box"):
            yield Label("Settings")
            yield Label("Risk Management Mode")
            yield Select(options=options, value=current_profile, allow_blank=False,
                         id="risk-select")
            yield Label("Cost-aware trading")
            yield Select(
                options=[("On", "on"), ("Off", "off")],
                value="on" if cfg.cost_aware else "off",
                allow_blank=False,
                id="cost-aware-select",
            )
            yield Label("Cost edge multiplier")
            yield Input(str(cfg.cost_edge_mult), id="cost-edge-mult")
            yield Label("Max cost-to-stop")
            yield Input(str(cfg.max_cost_to_stop), id="max-cost-to-stop")
            yield Label("", id="settings-error")
            with Horizontal():
                yield Button("Save", id="btn-save")
                yield Button("Cancel", id="btn-cancel")

    def _cost_config(self) -> tuple[BotConfig | None, str | None]:
        """Parse the cost fields into a new config; ``(None, problem)`` on error."""
        cfg = self.app.runner.config  # type: ignore
        try:
            cost_aware = self.query_one("#cost-aware-select", Select).value == "on"
            cost_edge_mult = float(self.query_one("#cost-edge-mult", Input).value)
            max_cost_to_stop = float(self.query_one("#max-cost-to-stop", Input).value)
        except ValueError:
            return None, "cost edge multiplier and max cost-to-stop must be numbers"
        selected = self.query_one("#risk-select", Select).value
        new_cfg = msgspec.structs.replace(
            cfg,
            risk_profile=str(selected) if selected is not None else cfg.risk_profile,
            cost_aware=cost_aware,
            cost_edge_mult=cost_edge_mult,
            max_cost_to_stop=max_cost_to_stop,
        )
        problems = validate(new_cfg)
        if problems:
            return None, "; ".join(problems)
        return new_cfg, None

    def _show_error(self, message: str) -> None:
        self.query_one("#settings-error", Label).update(message)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("btn-cancel", "cancel"):
            self.dismiss(None)
        elif event.button.id in ("btn-save", "save"):
            if self._saving:
                return
            self._saving = True
            try:
                new_cfg, problem = self._cost_config()
                if new_cfg is None:
                    self._show_error(problem or "invalid cost settings")
                    self._saving = False
                    return

                selected_profile = str(self.query_one("#risk-select", Select).value)
                current_profile = self.app.runner.risk.profile.name.lower()  # type: ignore

                def _apply() -> None:
                    problems = self.app.runner.apply_config(new_cfg)  # type: ignore
                    if problems:
                        self._show_error("; ".join(problems))
                        self._saving = False
                        return
                    self.app.query_one(TradeLog).log_line(
                        f"cost settings updated: cost_aware={new_cfg.cost_aware}, "
                        f"edge mult {new_cfg.cost_edge_mult:g}, "
                        f"max cost-to-stop {new_cfg.max_cost_to_stop:g}"
                    )
                    for warning in warnings(new_cfg):
                        self.app.query_one(TradeLog).log_line(f"warning: {warning}")
                    self._show_error("")
                    self._saving = False
                    self.dismiss(None)

                if selected_profile != current_profile:
                    self.app.push_screen(
                        ConfirmRiskScreen(
                            selected_profile.capitalize(),
                            _apply,
                            lambda: setattr(self, "_saving", False),
                        )
                    )
                else:
                    _apply()
            except Exception:
                self._saving = False
                raise
