from __future__ import annotations

from typing import Any
import msgspec
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Label, Select, Switch, Input, Button

from entropy.app import AppConfig
from entropy.config import EngineConfig
from entropy.strategy.engine import Strategy, StrategyConfig

from .charts import PriceChart, VolumeChart

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
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saving = False

    def compose(self) -> ComposeResult:
        cfg = self.app.cfg # type: ignore
        theme_options = [
            ("Entropy (Default)", "entropy"),
            ("Dracula", "dracula"),
            ("Cyberpunk", "cyberpunk"),
            ("Nord", "nord"),
            ("Forest", "forest"),
            ("Monochrome", "monochrome"),
            ("Sweet", "sweet")
        ]
        chart_options = [
            ("Candlestick", "candlestick"),
            ("Line Plot", "line")
        ]

        with Vertical(id="settings-container"):
            yield Static("Settings & Calibration Panel", id="settings-title")
            
            with Vertical(id="settings-form"):
                # Theme
                with Horizontal(classes="settings-row"):
                    yield Label("Visual Theme:")
                    yield Select(options=theme_options, value=cfg.theme, id="set-theme", allow_blank=False)
                
                # Chart mode
                with Horizontal(classes="settings-row"):
                    yield Label("Chart Style:")
                    yield Select(options=chart_options, value=cfg.chart_type, id="set-chart", allow_blank=False)
                
                # Volume toggle
                with Horizontal(classes="settings-row"):
                    yield Label("Show Volume Charts:")
                    yield Switch(value=cfg.show_volume, id="set-volume")

                # Risk management mode
                with Horizontal(classes="settings-row"):
                    yield Label("Risk Management Mode:")
                    yield Select(
                        options=[("Frosty", "frosty"), ("Medium", "medium"), ("Extreme", "extreme")],
                        value=cfg.risk_profile,
                        id="set-risk",
                        allow_blank=False
                    )
                
                # Equities feed toggle
                with Horizontal(classes="settings-row"):
                    yield Label("Enable Equities Feed:")
                    yield Switch(value=cfg.enable_equities, id="set-equities")

                # Crypto feed toggle
                with Horizontal(classes="settings-row"):
                    yield Label("Enable Live Crypto Feed:")
                    yield Switch(value=cfg.enable_crypto, id="set-crypto")

                # Equities Sim TPS
                with Horizontal(classes="settings-row"):
                    yield Label("Equity Sim Ticks/Sec (TPS):")
                    yield Input(value=str(cfg.equity_tps), id="set-tps")

                # Equity Strategy Symbol
                with Horizontal(classes="settings-row"):
                    yield Label("Equity Strategy Symbol:")
                    yield Input(value=cfg.strategy_symbol, id="set-strat-sym")

                # Crypto Strategy Symbol
                with Horizontal(classes="settings-row"):
                    yield Label("Crypto Strategy Symbol:")
                    yield Input(value=cfg.crypto_strategy_symbol, id="set-crypto-sym")

                # Engine Spike threshold
                with Horizontal(classes="settings-row"):
                    yield Label("Engine Spike % Threshold:")
                    yield Input(value=str(cfg.engine.spike_pct), id="set-spike")

                # Engine Snapdrop threshold
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
            
        if event.button.id == "btn-save":
            if self._saving:
                return
            self._saving = True
            try:
                # Read form values
                theme_val = self.query_one("#set-theme", Select).value
                chart_val = self.query_one("#set-chart", Select).value
                vol_val = self.query_one("#set-volume", Switch).value
                risk_val = self.query_one("#set-risk", Select).value
                equities_val = self.query_one("#set-equities", Switch).value
                crypto_val = self.query_one("#set-crypto", Switch).value
                tps_val = int(self.query_one("#set-tps", Input).value)
                strat_sym_val = self.query_one("#set-strat-sym", Input).value.upper()
                crypto_sym_val = self.query_one("#set-crypto-sym", Input).value
                spike_val = float(self.query_one("#set-spike", Input).value)
                snap_val = float(self.query_one("#set-snapdrop", Input).value)

                # Recreate engine config
                old_engine_cfg = self.app.cfg.engine # type: ignore
                new_engine_cfg = msgspec.structs.replace(
                    old_engine_cfg,
                    spike_pct=spike_val,
                    snapdrop_pct=snap_val
                )

                # Recreate app config
                new_cfg = msgspec.structs.replace(
                    self.app.cfg, # type: ignore
                    theme=str(theme_val),
                    chart_type=str(chart_val),
                    show_volume=vol_val,
                    risk_profile=str(risk_val),
                    enable_equities=equities_val,
                    enable_crypto=crypto_val,
                    equity_tps=tps_val,
                    strategy_symbol=strat_sym_val,
                    crypto_strategy_symbol=crypto_sym_val,
                    engine=new_engine_cfg
                )

                def apply_changes() -> None:
                    # Hot-apply config
                    app = self.app # type: ignore
                    app.cfg = new_cfg
                    app.risk_profile = str(risk_val)
                    
                    # Apply theme dynamically
                    app.theme = str(theme_val)
                    
                    # Update chart settings using query_default to prevent NoMatches flakiness
                    app.query_default("#price", PriceChart).chart_type = chart_val
                    app.query_default("#price2", PriceChart).chart_type = chart_val
                    
                    # Show/hide volume charts
                    app.query_default("#volume", VolumeChart).display = vol_val
                    app.query_default("#volume2", VolumeChart).display = vol_val
                    
                    # Apply equity sim TPS on the fly
                    if hasattr(app, "_equity") and app._equity is not None:
                        app._equity.tps = tps_val
                    
                    # Update engine configurations
                    app.engine.cfg = new_engine_cfg
                    
                    # If equity strategy symbol changed, reset strategy and re-warmup
                    if app.strategy.cfg.symbol != strat_sym_val:
                        app.strategy = Strategy(StrategyConfig(symbol=strat_sym_val))
                        app._warmup_strategies()

                    # If crypto strategy symbol changed, reset crypto strategy
                    if app.crypto_strategy.cfg.symbol != crypto_sym_val:
                        app.crypto_strategy = Strategy(StrategyConfig(symbol=crypto_sym_val, fee_bps=1.0))
                        app._warmup_crypto()

                if risk_val != self.app.cfg.risk_profile:
                    def handle_confirm(confirmed: bool | None) -> None:
                        if confirmed:
                            apply_changes()
                            self.dismiss()
                        else:
                            self._saving = False
                    
                    selected_profile = str(risk_val).capitalize()
                    self.app.push_screen(
                        SettingsConfirmScreen(selected_profile),
                        callback=handle_confirm
                    )
                else:
                    apply_changes()
                    self.dismiss()
            except ValueError as e:
                # Basic error feedback: highlight input error or show in status/errors
                self._saving = False
                self.app.push_screen(ErrorScreen(f"Invalid input: {e}", id="errors"))
            except Exception:
                self._saving = False
                raise


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


class SettingsConfirmScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "dismiss_cancel", "Cancel")]

    def __init__(self, selected_profile: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.selected_profile = selected_profile

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-container"):
            yield Static(
                f"Are you sure with that '{self.selected_profile}' risk management mode?",
                id="confirm-message"
            )
            with Horizontal(id="confirm-buttons"):
                yield Button("Confirm", variant="success", id="btn-confirm")
                yield Button("Cancel", variant="error", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        elif event.button.id == "btn-cancel":
            self.dismiss(False)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(False)

