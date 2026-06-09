from __future__ import annotations

from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical

from ..config import BotConfig
from ..runner import BotRunner
from .confirm import ConfirmRiskScreen
from .widgets import PnLPanel, PositionsTable, RiskBanner, TradeLog

_KEY_TO_PROFILE = {"1": "conservative", "2": "balanced", "3": "aggressive"}


class BotDashboard(App[None]):
    BINDINGS = [
        ("1", "risk('1')", "Conservative"),
        ("2", "risk('2')", "Balanced"),
        ("3", "risk('3')", "Aggressive"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: BotConfig | None = None,
                 runner: BotRunner | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cfg = config or BotConfig()
        self.runner = runner or BotRunner(self.cfg)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield RiskBanner(id="risk-banner")
            yield PnLPanel(id="pnl")
            yield PositionsTable(id="positions")
            yield TradeLog(id="trades")

    def on_mount(self) -> None:
        self.query_one(RiskBanner).set_profile(self.runner.risk.profile)
        self.set_interval(1 / 10, self._sample)
        self._run_feeds()

    def _sample(self) -> None:
        snap = self.runner.snapshot()
        self.query_one(PnLPanel).show(snap.portfolio)
        self.query_one(PositionsTable).show(snap.portfolio)

    @work(exclusive=True, group="bot")
    async def _run_feeds(self) -> None:
        await self.runner.run()

    def action_risk(self, key: str) -> None:
        name = _KEY_TO_PROFILE.get(key)
        if name is None:
            return
        self.push_screen(ConfirmRiskScreen(name, lambda: self.apply_risk_change(name)))

    def apply_risk_change(self, name: str) -> None:
        profile = self.runner.set_risk_profile(name)
        self.query_one(RiskBanner).set_profile(profile)
        self.query_one(TradeLog).log_line(f"risk profile changed -> {profile.name}")
