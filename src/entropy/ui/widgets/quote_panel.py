"""Quote detail panel: focus symbol, asset chip, last/Δ%, session hi-lo,
and (live equities only) a lazily fetched fundamentals line.

Sits between the two chart pairs in the right column. The app refreshes it
from ``sample_snapshot`` with cheap cached reads — only the fundamentals
fetch is async, running in a background worker on the app (group
``"fundamentals"``) through an injectable ``app._fundamentals_fetcher``.
"""

from __future__ import annotations

import msgspec
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

# One fundamentals fetch per symbol per TTL window (seconds).
FUNDAMENTALS_TTL_S = 600.0

_DASH = "—"


class Fundamentals(msgspec.Struct, frozen=True):
    """Equity snapshot metrics for the panel's fundamentals line
    (None = the source didn't report that field)."""

    pe: float | None = None
    market_cap: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None


class QuoteState(msgspec.Struct, frozen=True):
    """Everything the panel renders; frozen so the reactive's equality check
    skips repaints while nothing changed (this refreshes at 10 Hz)."""

    symbol: str = ""
    asset: str = ""          # "EQUITY" | "CRYPTO" | "SIM"
    last: float | None = None
    pct: float | None = None
    hi: float | None = None
    lo: float | None = None
    fundamentals: Fundamentals | None = None
    show_fundamentals: bool = False   # equities on the live source only


# google_finance Fundamental.tag -> Fundamentals field name
_FUND_TAGS = {
    "pe_ratio": "pe",
    "market_cap": "market_cap",
    "52_week_high": "high_52w",
    "52_week_low": "low_52w",
}


async def fetch_fundamentals_google(symbol: str) -> Fundamentals | None:
    """One-shot Google Finance fundamentals fetch (the app's default fetcher).

    Design choice: stockodile's GoogleFinanceProvider only *emits*
    fundamentals through its endless polling ``run()`` loop, but
    ``_scrape_symbol()`` is a self-contained single fetch that RETURNS the
    parsed records (``run()`` merely loops over it), so we drive it directly
    with our own short-lived aiohttp session. The Yahoo alternative
    (``YahooClient.fetch_financial_statements``) was rejected: it yields
    statement-level facts (revenue / balance sheet / cash flow), not the
    P/E · MktCap · 52w snapshot this panel shows.

    Returns None when the page yields no mapped fundamentals; raises on
    network errors (the app's worker downgrades those to a debug log).
    """
    # Lazy imports: the sim path must never pay for stockodile/aiohttp.
    import aiohttp
    from stockodile.providers.google_finance.connector import GoogleFinanceProvider
    from stockodile.reference.registry import InstrumentRegistry
    from stockodile.schema.records import Fundamental as StkFundamental
    from stockodile.sink.base import MemorySink

    provider = GoogleFinanceProvider(
        [symbol], ["fundamental"], MemorySink(), InstrumentRegistry()
    )
    async with aiohttp.ClientSession() as session:
        provider.session = session
        records = await provider._scrape_symbol(symbol)  # noqa: SLF001
    values: dict[str, float] = {}
    for rec in records:
        if not isinstance(rec, StkFundamental):
            continue
        field = _FUND_TAGS.get(rec.tag)
        if field is not None and field not in values:
            values[field] = rec.val
    if not values:
        return None
    return Fundamentals(**values)


def format_compact(value: float) -> str:
    """Humanize a big number ticker-style: 3.42T / 456.70B / 12.30M."""
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= cut:
            return f"{value / cut:.2f}{suffix}"
    return f"{value:.2f}"


def fundamentals_line(fund: Fundamentals | None) -> str:
    """The panel's 4th line; ``—`` placeholders stand in while loading or
    when a field is missing."""
    if fund is None:
        return f"P/E {_DASH} · MktCap {_DASH} · 52w {_DASH}/{_DASH}"
    pe = f"{fund.pe:.1f}" if fund.pe is not None else _DASH
    cap = format_compact(fund.market_cap) if fund.market_cap is not None else _DASH
    hi = f"{fund.high_52w:.2f}" if fund.high_52w is not None else _DASH
    lo = f"{fund.low_52w:.2f}" if fund.low_52w is not None else _DASH
    return f"P/E {pe} · MktCap {cap} · 52w {hi}/{lo}"


class QuotePanel(Widget):
    """Compact quote-detail readout for the app's focus symbol."""

    state: reactive[QuoteState] = reactive(QuoteState())

    def watch_state(self, *_: object) -> None:
        self.refresh()

    def render(self) -> Text:
        s = self.state
        theme = self.app.theme_variables
        accent = theme.get("accent", "#e6c200")
        foreground = theme.get("foreground", "#c8c8c8")
        success = theme.get("success", "#26d626")
        error = theme.get("error", "#ff3b3b")

        t = Text()
        t.append(s.symbol or _DASH, style=f"bold {foreground}")
        if s.asset:
            t.append(f"  {s.asset} ", style=f"bold reverse {accent}")
        t.append("\n")
        t.append(f"Last {s.last:.2f}" if s.last is not None else f"Last {_DASH}",
                 style=foreground)
        if s.pct is not None:
            t.append(f"  {s.pct:+.2f}%", style=success if s.pct >= 0 else error)
        else:
            t.append(f"  {_DASH}", style=foreground)
        t.append("\n")
        hi = f"{s.hi:.2f}" if s.hi is not None else _DASH
        lo = f"{s.lo:.2f}" if s.lo is not None else _DASH
        t.append(f"Hi {hi}  Lo {lo}", style=foreground)
        if s.show_fundamentals:
            t.append("\n")
            t.append(fundamentals_line(s.fundamentals), style="#7a7a7a")
        return t
