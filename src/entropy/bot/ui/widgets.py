from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import DataTable, RichLog, Static

from ..portfolio import PortfolioSnapshot
from ..risk.profiles import MEDIUM, RiskProfile


class ModeBanner(Static):
    """Always-on PAPER/LIVE indicator so simulated results are never mistaken for real money."""

    mode: reactive[str] = reactive("paper")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if self.is_attached:
            self.update(self.banner_text())

    def banner_text(self) -> Text:
        is_live = self.mode == "live"
        label = "LIVE — REAL MONEY" if is_live else "PAPER (simulated, no real money)"
        return Text(f"MODE: {label}", style=f"bold {'red' if is_live else 'green'}")

    def on_mount(self) -> None:
        self.update(self.banner_text())


class RiskBanner(Static):
    """Always-on, colored risk-level banner."""

    profile_name: reactive[str] = reactive(MEDIUM.name)
    color: reactive[str] = reactive(MEDIUM.color)
    halted: reactive[bool] = reactive(False)

    def set_profile(self, profile: RiskProfile) -> None:
        self.profile_name = profile.name
        self.color = profile.color
        if self.is_attached:
            self.update(Text(self.render_text(), style=f"bold {self.color}"))

    def set_halted(self) -> None:
        self.halted = True
        self.color = "red"
        if self.is_attached:
            self.update(Text(self.render_text(), style=f"bold red"))

    def render_text(self) -> str:
        if self.halted:
            return "RISK PROFILE: HALTED"
        return f"RISK PROFILE: {self.profile_name.upper()}"

    def on_mount(self) -> None:
        color = "red" if self.halted else self.color
        self.update(Text(self.render_text(), style=f"bold {color}"))


class PnLPanel(Static):
    def show(self, snap: PortfolioSnapshot) -> None:
        self.update(
            f"Equity {snap.equity:,.2f}   Cash {snap.cash:,.2f}   "
            f"Realized {snap.realized_pnl:+,.2f}   Unrealized {snap.unrealized_pnl:+,.2f}   "
            f"Day {snap.daily_pnl:+,.2f}   Open {snap.open_count}"
        )


class PositionsTable(DataTable[str]):
    def on_mount(self) -> None:
        self.cursor_type = "none"
        self.zebra_stripes = False
        self.add_columns("Symbol", "Side", "Qty", "Entry", "Mark", "uPnL")

    def show(self, snap: PortfolioSnapshot) -> None:
        self.clear()
        for p in snap.positions:
            self.add_row(p.symbol, p.side.value, f"{p.qty:.4f}", f"{p.entry_px:.2f}",
                         f"{p.mark_px:.2f}", f"{p.unrealized_pnl:+.2f}")


class TradeLog(RichLog):
    def log_line(self, text: str) -> None:
        self.write(text)
