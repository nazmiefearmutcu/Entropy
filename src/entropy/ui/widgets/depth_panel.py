"""Market-depth panel: a compact DOM-style bid/ask ladder for the focus symbol,
backed by stockodile's ``depth`` capability (new in stockodile 0.2.0).

Two data regimes share ONE render path — only the badge differs:

* **Synthetic** (keyless default): stockodile synthesizes a volume-at-price
  ladder from free Yahoo 1-minute bars (``basis="yahoo_1m_vap"``,
  ``is_synthetic=True``). This is *relative* liquidity — where volume
  historically concentrated — NOT real resting orders. Badged ``SYNTH``.
* **Real L1** (when ``ALPACA_API_KEY``/``ALPACA_API_SECRET`` are set):
  stockodile upgrades the same surface to Alpaca top-of-book with no code
  change (``basis="alpaca_l1"``, ``is_synthetic=False``). Badged ``L1``.

Hidden by default (``AppConfig.show_depth``); the ``:depth`` command toggles it
and force-refreshes. The app fetches it exactly like the fundamentals line: a
lazy, TTL-cached, injectable one-shot async fetch on an exclusive worker, so
the 10 Hz UI loop never blocks and a scrape hiccup can never crash the TUI.
"""

from __future__ import annotations

import msgspec
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from entropy.ui.widgets.quote_panel import format_compact

# One depth snapshot per symbol per TTL window (seconds). Shorter than the
# fundamentals TTL: depth is a live microstructure read, not a slow-moving
# fundamental, so it should refresh more eagerly while a symbol holds focus.
DEPTH_TTL_S = 20.0

# Levels shown per side and the relative-volume bar width (chars). Kept small so
# the panel fits the packed charts column without starving the price charts.
DISPLAY_LEVELS = 6
BAR_WIDTH = 12

_DASH = "—"
_Level = tuple[float, float]  # (price, size)


class DepthView(msgspec.Struct, frozen=True):
    """Everything the panel renders, decoupled from stockodile's DepthProfile.

    Frozen so the reactive's equality check skips repaints while nothing
    changed (this panel is repainted from the 10 Hz snapshot). ``bids``/``asks``
    are ``(price, size)`` pairs in stockodile's native order (bids price-
    descending, asks price-ascending); the render sorts defensively regardless.
    """

    symbol: str = ""
    basis: str = ""
    is_synthetic: bool = True
    reference_price: float | None = None
    bids: tuple[_Level, ...] = ()
    asks: tuple[_Level, ...] = ()


class DepthRow(msgspec.Struct, frozen=True):
    """One rendered ladder row plus a semantic ``kind`` the widget maps to a
    theme colour. Splitting layout (pure, here) from styling (needs theme, in
    ``render``) keeps the ladder geometry unit-testable without an app."""

    text: str
    kind: str  # "badge" | "ask" | "mid" | "bid" | "empty"


async def fetch_depth(
    symbol: str, *, bins: int = 40, top_n: int = DISPLAY_LEVELS, method: str = "uniform"
) -> DepthView | None:
    """One-shot depth snapshot for ``symbol`` (the app's default fetcher).

    ``select_depth_source`` transparently returns the Alpaca L1 source when both
    Alpaca env keys are present, else the keyless synthetic source — the same
    "upgrade without code change" switch stockodile exposes. Lazy-imported so
    the sim path never pays for stockodile/aiohttp.

    Returns ``None`` when the snapshot carries no levels; PROPAGATES exceptions
    (no bars, auth failure, network error) so the app's worker can downgrade
    them to a debug log + cached ``None`` exactly like the fundamentals fetch.
    """
    from stockodile.depth import select_depth_source

    source = select_depth_source(bins=bins, top_n=top_n, method=method)
    profile = await source.snapshot(symbol)
    if not profile.bids and not profile.asks:
        return None
    return DepthView(
        symbol=symbol.upper(),
        basis=profile.basis,
        is_synthetic=profile.is_synthetic,
        reference_price=profile.reference_price,
        bids=tuple((float(p), float(s)) for p, s in profile.bids),
        asks=tuple((float(p), float(s)) for p, s in profile.asks),
    )


def _bar(size: float, max_size: float, width: int = BAR_WIDTH) -> str:
    """Relative-volume block bar, ``size`` scaled against the ladder's max."""
    if max_size <= 0 or size <= 0:
        return ""
    filled = round(width * (size / max_size))
    return "█" * max(1, min(width, filled))  # at least one block for any >0


def _level_row(price: float, size: float, max_size: float, kind: str) -> DepthRow:
    bar = _bar(size, max_size)
    text = f"{price:>9.2f} {bar:<{BAR_WIDTH}} {format_compact(size):>8}"
    return DepthRow(text=text, kind=kind)


def depth_rows(
    view: DepthView | None, *, max_levels: int = DISPLAY_LEVELS, symbol: str = ""
) -> list[DepthRow]:
    """Pure ladder layout: badge, asks (high→low), mid, bids (high→low).

    Returns rows top-to-bottom exactly as a DOM ladder reads. A ``None`` view or
    one with no levels yields a single ``—`` placeholder under the badge. The
    ``symbol`` override lets the badge still name the focus symbol while a fetch
    is in flight (view is ``None``) — matching the quote panel, which always
    shows its symbol; pass ``""`` (the ineligible case) to fall back to ``—``.
    """
    label = view.symbol if (view is not None and view.symbol) else symbol
    if view is None or (not view.bids and not view.asks):
        return [
            DepthRow(text=f"DEPTH {label or _DASH}", kind="badge"),
            DepthRow(text=_DASH, kind="empty"),
        ]

    mode = "SYNTH" if view.is_synthetic else "L1"
    badge = f"DEPTH {view.symbol}  {mode}·{view.basis}"

    # Defensive re-sort: asks ascending, bids descending, independent of source.
    asks = sorted((lv for lv in view.asks), key=lambda lv: lv[0])[:max_levels]
    bids = sorted((lv for lv in view.bids), key=lambda lv: -lv[0])[:max_levels]
    max_size = max((s for _, s in (*asks, *bids)), default=0.0)

    rows: list[DepthRow] = [DepthRow(text=badge, kind="badge")]
    # Asks highest-at-top: reverse the ascending slice so the best ask sits
    # just above the mid line.
    for price, size in reversed(asks):
        rows.append(_level_row(price, size, max_size, "ask"))

    ref = view.reference_price
    mid_text = f"{'─' * 6} {ref:.2f} " if ref is not None else f"{'─' * 6} {_DASH} "
    if not view.is_synthetic and asks and bids:
        # Real L1: a genuine spread exists (best_ask - best_bid).
        mid_text += f"spread {asks[0][0] - bids[0][0]:.2f} "
    else:
        mid_text += "rel.liq "
    rows.append(DepthRow(text=mid_text + "─" * 6, kind="mid"))

    for price, size in bids:  # already descending: best bid just below mid
        rows.append(_level_row(price, size, max_size, "bid"))
    return rows


class DepthPanel(Widget):
    """Compact DOM-style depth ladder for the app's focus symbol."""

    view: reactive[DepthView | None] = reactive(None)
    # Focus symbol shown on the badge while a fetch is in flight (view is None
    # but the symbol IS eligible); "" for the ineligible crypto/sim case.
    symbol: reactive[str] = reactive("")

    def watch_view(self, *_: object) -> None:
        # layout=True so a height:auto panel RE-MEASURES when the ladder's line
        # count changes (placeholder 2 -> L1 4 -> synthetic up to 14); a plain
        # refresh() only repaints and would freeze the panel at its first
        # (placeholder) height, clipping the ladder.
        self.refresh(layout=True)

    def watch_symbol(self, *_: object) -> None:
        self.refresh()  # badge text only; the ladder height is unchanged

    def render(self) -> Text:
        theme = self.app.theme_variables
        accent = theme.get("accent", "#e6c200")
        foreground = theme.get("foreground", "#c8c8c8")
        success = theme.get("success", "#26d626")
        error = theme.get("error", "#ff3b3b")
        muted = "#7a7a7a"
        style_by_kind = {
            "badge": f"bold {accent}",
            "ask": error,
            "mid": f"bold {accent}",
            "bid": success,
            "empty": muted,
        }

        t = Text()
        rows = depth_rows(self.view, symbol=self.symbol)
        for i, row in enumerate(rows):
            if i:
                t.append("\n")
            t.append(row.text, style=style_by_kind.get(row.kind, foreground))
        return t
