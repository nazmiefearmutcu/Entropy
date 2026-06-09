from __future__ import annotations

from ..orders import Fill, Order

LIVE_WARNING = (
    "⚠️  LIVE TRADING IS DISABLED BY DEFAULT.\n"
    "    Enabling live mode places REAL orders with REAL money on a real exchange.\n"
    "    You are solely responsible for all financial risk, losses, and exchange fees.\n"
    "    This software is provided \"as is\", with NO warranty. Past simulated\n"
    "    performance does NOT guarantee future results.\n"
    "    To enable, you must explicitly set live.enabled = true AND pass\n"
    "    --i-understand-the-risk. The bot will never enable live trading on its own."
)


class LiveTradingDisabledError(RuntimeError):
    """Raised whenever a live order is attempted without full, explicit authorization."""


class LiveExecutor:
    """Disabled-by-default scaffold for live exchange execution.

    Requires three explicit opt-ins (enabled + acknowledged_risk + credentials).
    Even when fully authorized, real order routing is intentionally NOT implemented:
    the bot must never auto-place a real-money order. Wiring an exchange API is left
    entirely to the user.
    """

    def __init__(self, *, enabled: bool = False, acknowledged_risk: bool = False,
                 api_key: str = "", api_secret: str = "") -> None:
        self.enabled = enabled
        self.acknowledged_risk = acknowledged_risk
        self.api_key = api_key
        self.api_secret = api_secret

    def submit(self, order: Order) -> Fill:
        if not self.enabled:
            raise LiveTradingDisabledError(LIVE_WARNING)
        if not self.acknowledged_risk:
            raise LiveTradingDisabledError(
                "Live trading requires explicit risk acknowledgement: pass "
                "--i-understand-the-risk before any real order can be sent."
            )
        if not (self.api_key and self.api_secret):
            raise LiveTradingDisabledError(
                "Missing API credentials: live trading needs a valid api_key and api_secret."
            )
        raise NotImplementedError(
            "Live exchange order routing is intentionally not implemented. The bot will "
            "never auto-place a real-money order; wire your exchange API here yourself."
        )
