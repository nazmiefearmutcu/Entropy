from __future__ import annotations

import math
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from entropy.engine.timeframe import TIMEFRAMES

_HELP = """Entropy — keys:
  s  Settings    ?/h  Help    e  Errors    q  Quit
  /  Symbol search    w  Watch/unwatch the focused symbol
  :  Command bar — chart SYM · watch SYM · unwatch SYM ·
     tf 1m|5m|15m|1h|4h · theme NAME · source sim|live|auto · depth [SYM] · help
Click a board or watchlist row to focus its symbol on chart #1.
Scanner: new highs/lows over 3 rolling windows + session (timeframe-selectable); \
spikes & snap-drops.
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
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saving = False

    def compose(self) -> ComposeResult:
        cfg = self.app.cfg  # type: ignore
        theme_options = [
            ("Entropy (Default)", "entropy"), ("Dracula", "dracula"),
            ("Cyberpunk", "cyberpunk"), ("Nord", "nord"), ("Forest", "forest"),
            ("Monochrome", "monochrome"), ("Sweet", "sweet"),
        ]
        chart_options = [("Candlestick", "candlestick"), ("Line Plot", "line")]
        tf_options = [(name, name) for name in TIMEFRAMES]
        equity_source_options = [
            ("Simulated", "sim"), ("Live (stockodile)", "live"),
            ("Auto (live while NYSE open)", "auto"),
        ]

        with Vertical(id="settings-container"):
            yield Static("Settings", id="settings-title")
            with Vertical(id="settings-form"):
                yield Static("Appearance", classes="settings-section")
                with Horizontal(classes="settings-row"):
                    yield Label("Visual Theme:")
                    yield Select(
                        options=theme_options, value=cfg.theme, id="set-theme", allow_blank=False
                    )
                with Horizontal(classes="settings-row"):
                    yield Label("Chart Style:")
                    yield Select(
                        options=chart_options,
                        value=cfg.chart_type,
                        id="set-chart",
                        allow_blank=False,
                    )
                with Horizontal(classes="settings-row"):
                    yield Label("Show Volume Charts:")
                    yield Switch(value=cfg.show_volume, id="set-volume")

                yield Static("Timeframe", classes="settings-section")
                with Horizontal(classes="settings-row"):
                    yield Label("Timeframe:")
                    yield Select(
                        options=tf_options,
                        value=cfg.timeframe,
                        id="set-timeframe",
                        allow_blank=False,
                    )

                yield Static("Data Feeds", classes="settings-section")
                with Horizontal(classes="settings-row"):
                    yield Label("Enable Equities Feed:")
                    yield Switch(value=cfg.enable_equities, id="set-equities")
                with Horizontal(classes="settings-row"):
                    yield Label("Equity Source:")
                    yield Select(
                        options=equity_source_options,
                        value=cfg.equity_source,
                        id="set-equity-source",
                        allow_blank=False,
                    )
                with Horizontal(classes="settings-row"):
                    yield Label("Enable Live Crypto Feed:")
                    yield Switch(value=cfg.enable_crypto, id="set-crypto")
                with Horizontal(classes="settings-row"):
                    yield Label("Equity Sim Ticks/Sec (TPS):")
                    yield Input(value=str(cfg.equity_tps), id="set-tps")
                with Horizontal(classes="settings-row"):
                    yield Label("Equity Strategy Symbol:")
                    yield Input(value=cfg.strategy_symbol, id="set-strat-sym")
                with Horizontal(classes="settings-row"):
                    yield Label("Crypto Strategy Symbol:")
                    yield Input(value=cfg.crypto_strategy_symbol, id="set-crypto-sym")

                yield Static("Scanner / Engine", classes="settings-section")
                with Horizontal(classes="settings-row"):
                    yield Label("Engine Spike % Threshold:")
                    yield Input(value=str(cfg.engine.spike_pct), id="set-spike")
                with Horizontal(classes="settings-row"):
                    yield Label("Engine Snapdrop % Threshold:")
                    yield Input(value=str(cfg.engine.snapdrop_pct), id="set-snapdrop")

            with Horizontal(id="settings-buttons"):
                yield Button("Save Changes", variant="success", id="btn-save")
                yield Button("Cancel", variant="error", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss()
            return
        if event.button.id != "btn-save":
            return
        if self._saving:
            return
        self._saving = True
        try:
            theme_val = str(self.query_one("#set-theme", Select).value)
            chart_val = str(self.query_one("#set-chart", Select).value)
            vol_val = self.query_one("#set-volume", Switch).value
            tf_val = str(self.query_one("#set-timeframe", Select).value)
            equities_val = self.query_one("#set-equities", Switch).value
            equity_source_val = str(self.query_one("#set-equity-source", Select).value)
            crypto_val = self.query_one("#set-crypto", Switch).value
            tps_val = int(self.query_one("#set-tps", Input).value)
            strat_sym_val = self.query_one("#set-strat-sym", Input).value.upper()
            crypto_sym_val = self.query_one("#set-crypto-sym", Input).value
            spike_val = float(self.query_one("#set-spike", Input).value)
            snap_val = float(self.query_one("#set-snapdrop", Input).value)
            if tps_val <= 0:
                raise ValueError("Equity TPS must be a positive integer")
            # float() happily parses "inf"/"nan"; NaN even slips past the <= 0
            # comparison below, poisoning the engine config — require finite first.
            # (TPS is int()-parsed, which already rejects inf/nan strings.)
            if not (math.isfinite(spike_val) and math.isfinite(snap_val)):
                raise ValueError("Spike/Snapdrop thresholds must be finite numbers")
            if spike_val <= 0 or snap_val <= 0:
                raise ValueError("Spike/Snapdrop thresholds must be positive")
            if not strat_sym_val or not crypto_sym_val:
                raise ValueError("Strategy symbols must not be empty")
        except ValueError as e:
            self._saving = False
            self.app.push_screen(ErrorScreen(f"Invalid input: {e}", id="errors"))
            return

        self.app._apply_settings(  # type: ignore
            theme=theme_val, chart_type=chart_val, show_volume=vol_val,
            timeframe=tf_val, enable_equities=equities_val, enable_crypto=crypto_val,
            equity_source=equity_source_val, equity_tps=tps_val, strategy_symbol=strat_sym_val,
            crypto_strategy_symbol=crypto_sym_val, spike_pct=spike_val, snapdrop_pct=snap_val,
        )
        self.dismiss()


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
