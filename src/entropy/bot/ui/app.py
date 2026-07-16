from __future__ import annotations

from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical

from ..config import BotConfig
from ..runner import BotRunner
from .confirm import ConfirmRiskScreen, BotSettingsScreen
from .widgets import ModeBanner, PnLPanel, PositionsTable, RiskBanner, TradeLog


class BotDashboard(App[None]):
    BINDINGS = [
        ("s", "settings", "Settings"),
        ("k", "trip_breaker", "Halt"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: BotConfig | None = None,
                 runner: BotRunner | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cfg = config or BotConfig()
        self.runner = runner or BotRunner(self.cfg)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield ModeBanner(id="mode-banner")
            yield RiskBanner(id="risk-banner")
            yield PnLPanel(id="pnl")
            yield PositionsTable(id="positions")
            yield TradeLog(id="trades")

    def on_mount(self) -> None:
        self.query_one(ModeBanner).set_mode(self.cfg.mode)
        self.query_one(RiskBanner).set_profile(self.runner.risk.profile)
        self.set_interval(1 / 10, self._sample)
        self._run_feeds()

    def _sample(self) -> None:
        if not self.is_running:
            return
        try:
            pnl = self.query_one(PnLPanel)
            pos = self.query_one(PositionsTable)
        except Exception:
            return
        snap = self.runner.snapshot()
        pnl.show(snap.portfolio)
        pos.show(snap.portfolio)

    @work(exclusive=True, group="bot")
    async def _run_feeds(self) -> None:
        await self.runner.run()

    def action_settings(self) -> None:
        self.push_screen(BotSettingsScreen())

    def apply_risk_change(self, name: str) -> None:
        profile = self.runner.set_risk_profile(name)
        self.query_one(RiskBanner).set_profile(profile)
        self.query_one(TradeLog).log_line(f"risk profile changed -> {profile.name}")

    def action_trip_breaker(self) -> None:
        self.runner.trip_circuit_breaker()
        self.query_one(TradeLog).log_line("EMERGENCY HALT: circuit breaker tripped")
        self.query_one(RiskBanner).set_halted()
