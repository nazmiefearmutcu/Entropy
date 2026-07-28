"""Wire types shared with the React frontend.

Every message the sidecar emits and every body it accepts is declared here as a
msgspec Struct, so the schema is single-sourced instead of being reconstructed
by hand on either side. FastAPI cannot use a msgspec.Struct as a body model, so
the routes decode request bodies with ``msgspec.json.decode(..., type=X)``.
"""

from __future__ import annotations

from typing import Any

import msgspec

Leader = tuple[str, int, float, float]      # (symbol, count, price, pct)
TickerEntry = tuple[str, list[tuple[str, int]]]  # (window, [(symbol, count)])
Candle = tuple[int, float, float, float, float, float]  # (ts_ns, o, h, l, c, v)
Level = tuple[float, float]                 # (price, size)
#: (symbol, last, pct, spark) — spark is the raw price ring, the frontend renders it.
# (canonical symbol, exchange ticker, instrument name, exchange badge,
#  last, pct, spark). The last three used to be all a row carried, which left
# the pane rendering "binance-s…" — the canonical id clipped to the venue.
WatchRow = tuple[str, str, str, str, float | None, float | None, list[float]]

#: Theme vocabulary, mirroring entropy.ui.theme.ALL_THEMES. Duplicated as plain
#: strings rather than imported: /api/meta must answer without pulling Textual's
#: theme objects into the sidecar's import graph just to read seven names.
THEMES: tuple[str, ...] = (
    "entropy", "dracula", "cyberpunk", "nord", "forest", "monochrome", "sweet",
)

#: Chart renderers the frontend implements (mirrors PriceChart.chart_type).
CHART_TYPES: tuple[str, ...] = ("candlestick", "line")

#: AppConfig.equity_source vocabulary.
EQUITY_SOURCES: tuple[str, ...] = ("sim", "live", "auto")


class DepthLevels(msgspec.Struct, frozen=True):
    basis: str
    is_synthetic: bool
    reference_price: float
    bids: list[Level]
    asks: list[Level]


class Fundamentals(msgspec.Struct, frozen=True):
    pe: float | None = None
    market_cap: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None


class FocusView(msgspec.Struct, frozen=True):
    symbol: str
    asset: str                              # "EQUITY" | "CRYPTO" | "SIM"
    last: float | None
    pct: float | None
    hi: float | None
    lo: float | None
    candles: list[Candle]
    depth: DepthLevels | None
    fundamentals: Fundamentals | None
    # Resolved chart interval (NOT necessarily the timeframe: chart_interval ""
    # follows it, anything else overrides it) plus the scanner's timeframe, so
    # the chart pane can label both without re-deriving the resolution rule.
    interval: str = ""
    timeframe: str = ""


class SettingsView(msgspec.Struct, frozen=True):
    """The AppConfig fields the live panes react to, echoed on every snapshot.

    The frontend reads its current state from the stream rather than re-fetching
    /api/settings after each mutation, so a change made from the command bar or
    a second window shows up everywhere within one tick.
    """

    timeframe: str = ""
    chart_interval: str = ""
    chart_type: str = "candlestick"
    show_volume: bool = True
    show_depth: bool = False
    equity_source: str = "auto"
    enable_equities: bool = True
    enable_crypto: bool = True
    theme: str = "entropy"


class FeedStatus(msgspec.Struct, frozen=True):
    """What the two feeds are actually doing right now.

    ``detail`` carries the last non-fatal explanation (live-feed fallback,
    provider name, a bot exception swallowed on the drain) so the UI can show
    WHY a feed is degraded instead of only that it is.
    """

    equities: str = "off"    # "off" | "sim" | "live" | "error"
    crypto: str = "off"      # "off" | "connecting" | "live" | "error"
    detail: str = ""


class BotPosition(msgspec.Struct, frozen=True):
    symbol: str
    side: str
    qty: float
    entry_px: float
    mark_px: float
    unrealized_pnl: float
    stop_px: float
    tp_px: float


class BotStrategy(msgspec.Struct, frozen=True):
    name: str
    warm: bool
    regimes: dict[str, str] = msgspec.field(default_factory=dict)
    directions: dict[str, int] = msgspec.field(default_factory=dict)


class BotView(msgspec.Struct, frozen=True):
    running: bool
    paused: bool
    halted: bool
    warm: bool
    mode: str
    timeframe: str
    bar_s: float
    risk_profile: str
    risk_description: str
    ticks: int
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    open_count: int
    positions: list[BotPosition] = msgspec.field(default_factory=list)
    strategies: list[BotStrategy] = msgspec.field(default_factory=list)
    last_signals: list[str] = msgspec.field(default_factory=list)
    last_rejects: list[str] = msgspec.field(default_factory=list)


class SnapshotMessage(msgspec.Struct, frozen=True, tag="snapshot", tag_field="type"):
    schema_version: int
    ts_ns: int
    buy_pct: float
    sell_pct: float
    raw_hz: float
    accel: str
    new_highs: list[Leader]
    new_lows: list[Leader]
    ticker: list[TickerEntry]
    focus: FocusView
    watchlist: list[WatchRow]
    market_status: str
    source: str
    settings: SettingsView = msgspec.field(default_factory=SettingsView)
    feeds: FeedStatus = msgspec.field(default_factory=FeedStatus)
    #: None until the bot has been started once — "never started" and "started
    #: then stopped" are different states and the dashboard shows them so.
    bot: BotView | None = None


class CommandRequest(msgspec.Struct, frozen=True):
    verb: str
    arg: str = ""


class CommandResult(msgspec.Struct, frozen=True):
    """Uniform reply for every mutating endpoint.

    ``ok`` is a claim about what actually happened. A verb that changed nothing
    reports ok=False with a reason — never a cheerful acknowledgement.
    """

    ok: bool
    message: str
    problems: list[str] = msgspec.field(default_factory=list)


class SymbolRequest(msgspec.Struct, frozen=True):
    symbol: str


class SettingsPatch(msgspec.Struct, frozen=True):
    """Partial settings update.

    Both halves arrive as raw mappings, not typed structs: a PUT carrying only
    ``{"app": {"theme": "nord"}}`` must leave every other field alone, and
    decoding straight into AppConfig would silently reset the rest to defaults.
    The route deep-merges onto the CURRENT config before converting.
    """

    app: dict[str, Any] | None = None
    bot: dict[str, Any] | None = None
