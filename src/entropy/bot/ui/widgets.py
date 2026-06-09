from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import DataTable, RichLog, Static

from ..portfolio import PortfolioSnapshot
from ..risk.profiles import BALANCED, RiskProfile


class RiskBanner(Static):
    """Always-on, colored risk-level banner."""

    profile_name: reactive[str] = reactive(BALANCED.name)
    color: reactive[str] = reactive(BALANCED.color)

    def set_profile(self, profile: RiskProfile) -> None:
        self.profile_name = profile.name
        self.color = profile.color
        if self.is_attached:
            self.update(Text(self.render_text(), style=f"bold {self.color}"))

    def render_text(self) -> str:
        return f"RISK PROFILE: {self.profile_name.upper()}"

    def on_mount(self) -> None:
        self.update(Text(self.render_text(), style=f"bold {self.color}"))


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
