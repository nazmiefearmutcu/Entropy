from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast

import msgspec
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Input,
    Label,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)
from textual.widgets.data_table import CellDoesNotExist

from entropy import settings as settings_store
from entropy.bot.config import (
    STRATEGY_NAMES,
    BotConfig,
    ConsensusConfig,
)
from entropy.bot.config import validate as validate_bot
from entropy.bot.risk.profiles import PRESETS
from entropy.engine.timeframe import CHART_INTERVALS, FOLLOW_TIMEFRAME, TIMEFRAMES

if TYPE_CHECKING:
    from entropy.ui.app import EntropyApp

_HELP = """Entropy — keys:
  s  Settings    b  Bot settings    ctrl+w  Watchlist manager
  ?/h  Help    e  Errors    q  Quit
  /  Symbol search (changes the chart symbol)    w  Watch/unwatch the focus
  :  Command bar — chart SYM · watch SYM · unwatch SYM ·
     tf 1m|5m|15m|1h|4h · theme NAME · source sim|live|auto · depth [SYM] · help
Click a board or watchlist row to focus its symbol on chart #1.
Settings tabs: General · Chart (candle interval) · Scanner · Feeds · Bot · Watchlist.
"""

_THEME_OPTIONS = [
    ("Entropy (Default)", "entropy"), ("Dracula", "dracula"),
    ("Cyberpunk", "cyberpunk"), ("Nord", "nord"), ("Forest", "forest"),
    ("Monochrome", "monochrome"), ("Sweet", "sweet"),
]
_CHART_OPTIONS = [("Candlestick", "candlestick"), ("Line Plot", "line")]
_EQUITY_SOURCE_OPTIONS = [
    ("Simulated", "sim"), ("Live (crocodile)", "live"),
    ("Auto (live while NYSE open)", "auto"),
]
_MODE_OPTIONS = [("Paper (simulated)", "paper"), ("Live (real money)", "live")]
_VOTE_MODE_OPTIONS = [
    ("Adaptive", "adaptive"), ("Trend following", "trend"),
    ("Mean reversion", "mean_revert"), ("Legacy", "legacy"),
]
_NORMALIZE_OPTIONS = [
    ("Participating", "participating"), ("All indicators", "total"),
]
_EXIT_MODE_OPTIONS = [
    ("Score reversal", "score"), ("Trend flip", "trend_flip"), ("Either", "either"),
]
_STRATEGY_LABELS = {
    "consensus": "Consensus (multi-indicator)",
    "ema_cross": "EMA cross",
    "momentum_scalper": "Momentum scalper",
    "black_scholes": "Black-Scholes (distribution)",
}

# The chart-interval Select leads with the follow-the-timeframe sentinel so the
# legacy coupled behaviour stays one click away.
_CHART_INTERVAL_OPTIONS = [("Follow timeframe", FOLLOW_TIMEFRAME)] + [
    (name, name) for name in CHART_INTERVALS
]

_NS_PER_S = 1_000_000_000


def _row(label: str, control: Widget) -> Horizontal:
    """One label + control line, matching the form's existing two-column look."""
    return Horizontal(Label(label), control, classes="settings-row")


def _text_row(label: str, widget_id: str, value: object) -> Horizontal:
    return _row(label, Input(value=str(value), id=widget_id))


def _as_int(raw: str, label: str, *, minimum: int | None = None) -> int:
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"{label} must be a whole number") from None
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return value


def _as_float(
    raw: str, label: str, *, minimum: float | None = None,
    maximum: float | None = None, positive: bool = False,
) -> float:
    try:
        value = float(raw.strip())
    except ValueError:
        raise ValueError(f"{label} must be a number") from None
    # float() happily parses "inf"/"nan", and NaN slips past every comparison
    # below — poisoning whatever config it lands in. Require finite first.
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if positive and value <= 0.0:
        raise ValueError(f"{label} must be positive")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum:g}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum:g}")
    return value


def _risk_description(name: str) -> str:
    profile = PRESETS.get(name.lower())
    return profile.description if profile is not None else f"Unknown risk profile {name!r}."


def bar_seconds_hint(timeframe: str, override_raw: str) -> str:
    """Live readout of the bar the bot will actually compute on.

    Mirrors ``BotConfig.bar_seconds()`` so the override field can never be
    silently ignored: an unparseable or non-positive override reads as
    "follow the timeframe", which is exactly what the runner would do.
    """
    try:
        override = float(override_raw.strip())
    except ValueError:
        override = 0.0
    if math.isfinite(override) and override > 0.0:
        return f"Effective bar: {override:g}s (explicit override)"
    spec = TIMEFRAMES.get(timeframe)
    if spec is None:
        return f"Effective bar: unknown timeframe {timeframe!r}"
    return f"Effective bar: {spec.bar_ns / _NS_PER_S:g}s (from the {timeframe} timeframe)"


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
    """Tabbed settings: General · Chart · Scanner · Feeds · Bot · Watchlist.

    Every control here writes through to the running app on Save (and, for the
    watchlist, immediately) — a knob that looks live but changes nothing is the
    exact complaint this screen exists to answer.
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *args: Any, initial_tab: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saving = False
        self._initial_tab = initial_tab

    @property
    def _entropy(self) -> EntropyApp:
        return cast("EntropyApp", self.app)

    # --- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-container"):
            yield Static("Settings", id="settings-title")
            with TabbedContent(initial=self._initial_tab, id="settings-tabs"):
                with TabPane("General", id="tab-general"):
                    yield from self._general_tab()
                with TabPane("Chart", id="tab-chart"):
                    yield from self._chart_tab()
                with TabPane("Scanner", id="tab-scanner"):
                    yield from self._scanner_tab()
                with TabPane("Feeds", id="tab-feeds"):
                    yield from self._feeds_tab()
                with TabPane("Bot", id="tab-bot"):
                    yield from self._bot_tab()
                with TabPane("Watchlist", id="tab-watchlist"):
                    yield from self._watchlist_tab()
            with Horizontal(id="settings-buttons"):
                yield Button("Save Changes", variant="success", id="btn-save")
                yield Button("Cancel", variant="error", id="btn-cancel")

    def _general_tab(self) -> ComposeResult:
        cfg = self._entropy.cfg
        yield Static("Appearance", classes="settings-section")
        yield _row("Visual Theme:", Select(
            options=_THEME_OPTIONS, value=cfg.theme, id="set-theme", allow_blank=False
        ))
        yield Static("Persistence", classes="settings-section")
        yield Static(
            f"Saved to {settings_store.default_path()}\n"
            "Shared with the desktop app and the bot runner.",
            id="settings-path", classes="settings-note",
        )

    def _chart_tab(self) -> ComposeResult:
        cfg = self._entropy.cfg
        yield Static("Rendering", classes="settings-section")
        yield _row("Chart Style:", Select(
            options=_CHART_OPTIONS, value=cfg.chart_type, id="set-chart", allow_blank=False
        ))
        yield _row("Show Volume Charts:", Switch(value=cfg.show_volume, id="set-volume"))
        yield _row("Show Depth Ladder:", Switch(value=cfg.show_depth, id="set-depth"))
        yield Static("Candles", classes="settings-section")
        yield _row("Candle Interval:", Select(
            options=_CHART_INTERVAL_OPTIONS, value=cfg.chart_interval,
            id="set-chart-interval", allow_blank=False,
        ))
        yield Static(
            "Candle width is independent of the scanner timeframe: 1m candles "
            "under a 15m scan is a normal way to trade.",
            classes="settings-note",
        )
        yield _text_row("Candles Retained:", "set-chart-bars", cfg.chart_bars)

    def _scanner_tab(self) -> ComposeResult:
        cfg = self._entropy.cfg
        yield Static("Timeframe", classes="settings-section")
        yield _row("Scanner Timeframe:", Select(
            options=[(name, name) for name in TIMEFRAMES], value=cfg.timeframe,
            id="set-timeframe", allow_blank=False,
        ))
        yield Static(
            "Drives the rolling high/low windows, momentum horizon and breadth "
            "window — not the chart's candle width.",
            classes="settings-note",
        )
        yield Static("Thresholds", classes="settings-section")
        yield _text_row("Engine Spike % Threshold:", "set-spike", cfg.engine.spike_pct)
        yield _text_row("Engine Snapdrop % Threshold:", "set-snapdrop", cfg.engine.snapdrop_pct)

    def _feeds_tab(self) -> ComposeResult:
        cfg = self._entropy.cfg
        yield Static("Equities", classes="settings-section")
        yield _row("Enable Equities Feed:", Switch(value=cfg.enable_equities, id="set-equities"))
        yield _row("Equity Source:", Select(
            options=_EQUITY_SOURCE_OPTIONS, value=cfg.equity_source,
            id="set-equity-source", allow_blank=False,
        ))
        yield _text_row("Equity Sim Ticks/Sec (TPS):", "set-tps", cfg.equity_tps)
        yield _text_row("Equity Strategy Symbol:", "set-strat-sym", cfg.strategy_symbol)
        yield Static("Crypto", classes="settings-section")
        yield _row("Enable Live Crypto Feed:", Switch(value=cfg.enable_crypto, id="set-crypto"))
        yield _text_row("Crypto Strategy Symbol:", "set-crypto-sym", cfg.crypto_strategy_symbol)

    def _bot_tab(self) -> ComposeResult:
        bot = self._entropy.bot_cfg
        c = bot.consensus
        yield Static("Mode", classes="settings-section")
        yield _row("Trading Mode:", Select(
            options=_MODE_OPTIONS, value=bot.mode, id="bot-mode", allow_blank=False
        ))
        yield Static(
            "Live mode sends real orders with real money. It stays refused "
            "until the acknowledgement below is on.",
            id="bot-live-warning", classes="settings-warning",
        )
        yield _row("I Accept Live Risk:", Switch(
            value=bot.live.acknowledged_risk, id="bot-live-ack"
        ))

        yield Static("Risk", classes="settings-section")
        yield _row("Risk Profile:", Select(
            options=[(p.name, key) for key, p in PRESETS.items()],
            value=bot.risk_profile if bot.risk_profile in PRESETS else "medium",
            id="bot-risk", allow_blank=False,
        ))
        yield Static(
            _risk_description(bot.risk_profile), id="bot-risk-desc", classes="settings-note"
        )

        yield Static("Strategies", classes="settings-section")
        for name in STRATEGY_NAMES:
            yield _row(
                f"{_STRATEGY_LABELS.get(name, name)}:",
                Switch(value=name in bot.strategies, id=f"bot-strat-{name}"),
            )

        yield Static("Cadence", classes="settings-section")
        yield _row("Bot Compute Timeframe:", Select(
            options=[(name, name) for name in TIMEFRAMES],
            value=bot.timeframe if bot.timeframe in TIMEFRAMES else "1m",
            id="bot-timeframe", allow_blank=False,
        ))
        yield Static(
            "How often the bot's strategies re-evaluate — separate from the "
            "scanner timeframe on the Scanner tab.",
            classes="settings-note",
        )
        yield _text_row("Bar Override (s, 0 = follow):", "bot-bar-s", bot.bar_s)
        yield Static(
            bar_seconds_hint(bot.timeframe, str(bot.bar_s)),
            id="bot-bar-effective", classes="settings-note",
        )

        yield Static("Account", classes="settings-section")
        yield _text_row("Starting Cash:", "bot-cash", bot.starting_cash)
        yield _text_row("Fee (bps):", "bot-fee", bot.fee_bps)
        yield _text_row("Slippage (bps):", "bot-slip", bot.slippage_bps)

        yield Static("EMA Cross / Momentum", classes="settings-section")
        yield _text_row("EMA Symbol:", "bot-ema-sym", bot.ema_symbol)
        yield _text_row("EMA Fast Period:", "bot-ema-fast", bot.ema_fast)
        yield _text_row("EMA Slow Period:", "bot-ema-slow", bot.ema_slow)
        yield _text_row("Momentum Min %:", "bot-momentum", bot.momentum_min_pct)

        yield Static("Signal Logic", classes="settings-section")
        yield _row("Vote Mode:", Select(
            options=_VOTE_MODE_OPTIONS, value=c.vote_mode, id="bot-vote-mode", allow_blank=False
        ))
        yield _row("Normalize Over:", Select(
            options=_NORMALIZE_OPTIONS, value=c.normalize, id="bot-normalize", allow_blank=False
        ))
        yield _text_row("Min Participation (0-1):", "bot-min-part", c.min_participation)
        yield _text_row("Signal Threshold (0-1]:", "bot-threshold", c.threshold)
        yield _text_row("Min Hold (bars):", "bot-min-hold", c.min_hold_bars)
        yield _text_row("Cooldown (bars):", "bot-cooldown", c.cooldown_bars)
        yield _row("Exit Mode:", Select(
            options=_EXIT_MODE_OPTIONS, value=c.exit_mode, id="bot-exit-mode", allow_blank=False
        ))

        # Indicators/regime are collapsed by default: they are the knobs almost
        # nobody touches, and showing all of them flat is what made the old
        # single-page form unreadable.
        with Collapsible(title="Indicators (advanced)", id="bot-indicators"):
            yield _text_row("Consensus EMA Fast:", "bot-ind-ema-fast", c.ema_fast)
            yield _text_row("Consensus EMA Slow:", "bot-ind-ema-slow", c.ema_slow)
            yield _text_row("MACD Fast:", "bot-macd-fast", c.macd_fast)
            yield _text_row("MACD Slow:", "bot-macd-slow", c.macd_slow)
            yield _text_row("MACD Signal:", "bot-macd-signal", c.macd_signal)
            yield _text_row("RSI Period:", "bot-rsi-period", c.rsi_period)
            yield _text_row("Bollinger Period:", "bot-bb-period", c.bb_period)
            yield _text_row("Bollinger Std Dev:", "bot-bb-std", c.bb_std)

        with Collapsible(title="Regime detection (advanced)", id="bot-regime"):
            yield _text_row("Move Floor:", "bot-move-floor", c.move_floor)
            yield _text_row("Trend Efficiency Ratio:", "bot-trend-er", c.trend_er)
            yield _text_row("Regime Window (bars):", "bot-regime-window", c.regime_window)
            yield _text_row("Regime Tilt:", "bot-regime-tilt", c.regime_tilt)

        yield Static("Reset", classes="settings-section")
        yield Button("Reset bot settings to defaults", id="btn-bot-reset", variant="warning")

    def _watchlist_tab(self) -> ComposeResult:
        yield Static("Followed symbols", classes="settings-section")
        yield DataTable(id="watch-table")
        yield Static("Add a symbol", classes="settings-section")
        yield _row("Symbol:", Input(placeholder="AAPL / binance-spot:BTCUSDT", id="watch-input"))
        with Horizontal(id="watch-buttons"):
            yield Button("Add", id="btn-watch-add", variant="success")
            yield Button("Remove selected", id="btn-watch-remove", variant="error")
        yield Static(
            "Watchlist edits apply immediately and are persisted; Save is only "
            "needed for the other tabs.",
            id="watch-status", classes="settings-note",
        )

    def on_mount(self) -> None:
        table = self.query_one("#watch-table", DataTable)
        table.add_columns("Symbol", "Instrument", "Exchange")
        table.cursor_type = "row"
        table.zebra_stripes = False
        self._refresh_watch_table()

    # --- live readouts -----------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "bot-risk":
            self.query_one("#bot-risk-desc", Static).update(_risk_description(str(event.value)))
        elif event.select.id == "bot-timeframe":
            self._refresh_bar_hint()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "bot-bar-s":
            self._refresh_bar_hint()

    def _refresh_bar_hint(self) -> None:
        timeframe = str(self.query_one("#bot-timeframe", Select).value)
        override = self.query_one("#bot-bar-s", Input).value
        self.query_one("#bot-bar-effective", Static).update(
            bar_seconds_hint(timeframe, override)
        )

    # --- watchlist tab -----------------------------------------------------

    def _refresh_watch_table(self) -> None:
        table = self.query_one("#watch-table", DataTable)
        table.clear()
        for info in self._entropy._watchlist.items():
            # Key stays the canonical id; the columns show the readable split.
            table.add_row(info.ticker or info.symbol, info.name or info.symbol,
                          info.exchange, key=info.symbol)

    def _watch_note(self, text: str) -> None:
        self.query_one("#watch-status", Static).update(text)

    def _selected_watch_symbol(self) -> str | None:
        table = self.query_one("#watch-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except CellDoesNotExist:
            return None  # cursor left behind by a row that was just removed
        return row_key.value

    def _add_watch(self) -> None:
        app = self._entropy
        raw = self.query_one("#watch-input", Input).value.strip()
        if not raw:
            self._watch_note("Type a symbol first.")
            return
        info = app._resolve_symbol(raw if ":" in raw else raw.upper())
        if info.symbol in app._watchlist:
            self._watch_note(f"[{info.symbol}] is already followed.")
            return
        if app.toggle_watch(info):
            self.query_one("#watch-input", Input).value = ""
            self._watch_note(f"Added [{info.symbol}].")
        else:
            self._watch_note(f"Could not add [{info.symbol}] — see Errors (e).")
        self._refresh_watch_table()

    def _remove_watch(self) -> None:
        app = self._entropy
        symbol = self._selected_watch_symbol()
        if symbol is None:
            self._watch_note("Select a row to remove.")
            return
        if app.toggle_watch(app._resolve_symbol(symbol)):
            self._watch_note(f"Could not remove [{symbol}] — see Errors (e).")
        else:
            self._watch_note(f"Removed [{symbol}].")
        self._refresh_watch_table()

    # --- bot tab -----------------------------------------------------------

    def _load_bot(self, bot: BotConfig) -> None:
        """Repopulate every Bot control from ``bot`` (the Reset button's worker)."""
        c = bot.consensus
        self.query_one("#bot-mode", Select).value = bot.mode
        self.query_one("#bot-live-ack", Switch).value = bot.live.acknowledged_risk
        self.query_one("#bot-risk", Select).value = bot.risk_profile
        self.query_one("#bot-timeframe", Select).value = bot.timeframe
        self.query_one("#bot-vote-mode", Select).value = c.vote_mode
        self.query_one("#bot-normalize", Select).value = c.normalize
        self.query_one("#bot-exit-mode", Select).value = c.exit_mode
        for name in STRATEGY_NAMES:
            self.query_one(f"#bot-strat-{name}", Switch).value = name in bot.strategies
        for widget_id, value in (
            ("#bot-bar-s", bot.bar_s), ("#bot-cash", bot.starting_cash),
            ("#bot-fee", bot.fee_bps), ("#bot-slip", bot.slippage_bps),
            ("#bot-ema-sym", bot.ema_symbol), ("#bot-ema-fast", bot.ema_fast),
            ("#bot-ema-slow", bot.ema_slow), ("#bot-momentum", bot.momentum_min_pct),
            ("#bot-min-part", c.min_participation), ("#bot-threshold", c.threshold),
            ("#bot-min-hold", c.min_hold_bars), ("#bot-cooldown", c.cooldown_bars),
            ("#bot-ind-ema-fast", c.ema_fast), ("#bot-ind-ema-slow", c.ema_slow),
            ("#bot-macd-fast", c.macd_fast), ("#bot-macd-slow", c.macd_slow),
            ("#bot-macd-signal", c.macd_signal), ("#bot-rsi-period", c.rsi_period),
            ("#bot-bb-period", c.bb_period), ("#bot-bb-std", c.bb_std),
            ("#bot-move-floor", c.move_floor), ("#bot-trend-er", c.trend_er),
            ("#bot-regime-window", c.regime_window), ("#bot-regime-tilt", c.regime_tilt),
        ):
            self.query_one(widget_id, Input).value = str(value)
        self.query_one("#bot-risk-desc", Static).update(_risk_description(bot.risk_profile))
        self._refresh_bar_hint()

    def _input_text(self, widget_id: str) -> str:
        return self.query_one(widget_id, Input).value

    def _collect_bot(self) -> BotConfig:
        """Build a BotConfig from the Bot tab; raises ValueError on a bad field.

        Field-level parsing only — cross-field sanity (fast < slow, live
        acknowledgement, …) is ``entropy.bot.config.validate``'s job, so the
        two never drift apart.
        """
        current = self._entropy.bot_cfg
        text = self._input_text
        strategies = tuple(
            name for name in STRATEGY_NAMES
            if self.query_one(f"#bot-strat-{name}", Switch).value
        )
        ema_symbol = text("#bot-ema-sym").strip()
        if not ema_symbol:
            raise ValueError("Bot EMA symbol must not be empty")
        consensus = ConsensusConfig(
            threshold=_as_float(text("#bot-threshold"), "Signal threshold",
                                positive=True, maximum=1.0),
            min_bars=current.consensus.min_bars,
            vote_mode=str(self.query_one("#bot-vote-mode", Select).value),
            normalize=str(self.query_one("#bot-normalize", Select).value),
            min_participation=_as_float(text("#bot-min-part"), "Min participation",
                                        minimum=0.0, maximum=1.0),
            w_ema=current.consensus.w_ema, w_macd=current.consensus.w_macd,
            w_rsi=current.consensus.w_rsi, w_bollinger=current.consensus.w_bollinger,
            ema_fast=_as_int(text("#bot-ind-ema-fast"), "Consensus EMA fast", minimum=1),
            ema_slow=_as_int(text("#bot-ind-ema-slow"), "Consensus EMA slow", minimum=1),
            macd_fast=_as_int(text("#bot-macd-fast"), "MACD fast", minimum=1),
            macd_slow=_as_int(text("#bot-macd-slow"), "MACD slow", minimum=1),
            macd_signal=_as_int(text("#bot-macd-signal"), "MACD signal", minimum=1),
            rsi_period=_as_int(text("#bot-rsi-period"), "RSI period", minimum=1),
            rsi_low=current.consensus.rsi_low, rsi_high=current.consensus.rsi_high,
            rsi_trend_low=current.consensus.rsi_trend_low,
            rsi_trend_high=current.consensus.rsi_trend_high,
            bb_period=_as_int(text("#bot-bb-period"), "Bollinger period", minimum=1),
            bb_std=_as_float(text("#bot-bb-std"), "Bollinger std dev", positive=True),
            bb_low=current.consensus.bb_low, bb_high=current.consensus.bb_high,
            bb_trend_low=current.consensus.bb_trend_low,
            bb_trend_high=current.consensus.bb_trend_high,
            move_floor=_as_float(text("#bot-move-floor"), "Move floor", minimum=0.0),
            trend_er=_as_float(text("#bot-trend-er"), "Trend efficiency ratio", minimum=0.0),
            regime_window=_as_int(text("#bot-regime-window"), "Regime window", minimum=1),
            slope_lookback=current.consensus.slope_lookback,
            regime_tilt=_as_float(text("#bot-regime-tilt"), "Regime tilt", minimum=0.0),
            min_hold_bars=_as_int(text("#bot-min-hold"), "Min hold bars", minimum=0),
            cooldown_bars=_as_int(text("#bot-cooldown"), "Cooldown bars", minimum=0),
            exit_mode=str(self.query_one("#bot-exit-mode", Select).value),
        )
        mode = str(self.query_one("#bot-mode", Select).value)
        acknowledged = self.query_one("#bot-live-ack", Switch).value
        return msgspec.structs.replace(
            current,
            mode=mode,
            risk_profile=str(self.query_one("#bot-risk", Select).value),
            strategies=strategies,
            starting_cash=_as_float(text("#bot-cash"), "Starting cash", positive=True),
            fee_bps=_as_float(text("#bot-fee"), "Fee (bps)", minimum=0.0),
            slippage_bps=_as_float(text("#bot-slip"), "Slippage (bps)", minimum=0.0),
            ema_symbol=ema_symbol,
            ema_fast=_as_int(text("#bot-ema-fast"), "Bot EMA fast", minimum=1),
            ema_slow=_as_int(text("#bot-ema-slow"), "Bot EMA slow", minimum=1),
            momentum_min_pct=_as_float(text("#bot-momentum"), "Momentum min %", minimum=0.0),
            timeframe=str(self.query_one("#bot-timeframe", Select).value),
            bar_s=_as_float(text("#bot-bar-s"), "Bar override", minimum=0.0),
            consensus=consensus,
            live=msgspec.structs.replace(
                current.live, enabled=(mode == "live"), acknowledged_risk=acknowledged
            ),
        )

    # --- events ------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-cancel":
            self.dismiss()
            return
        if button_id == "btn-watch-add":
            self._add_watch()
            return
        if button_id == "btn-watch-remove":
            self._remove_watch()
            return
        if button_id == "btn-bot-reset":
            self._load_bot(BotConfig())
            return
        if button_id != "btn-save" or self._saving:
            return
        self._saving = True
        try:
            theme_val = str(self.query_one("#set-theme", Select).value)
            chart_val = str(self.query_one("#set-chart", Select).value)
            vol_val = self.query_one("#set-volume", Switch).value
            depth_val = self.query_one("#set-depth", Switch).value
            chart_interval_val = str(self.query_one("#set-chart-interval", Select).value)
            chart_bars_val = _as_int(
                self.query_one("#set-chart-bars", Input).value, "Candles retained", minimum=10
            )
            tf_val = str(self.query_one("#set-timeframe", Select).value)
            equities_val = self.query_one("#set-equities", Switch).value
            equity_source_val = str(self.query_one("#set-equity-source", Select).value)
            crypto_val = self.query_one("#set-crypto", Switch).value
            tps_val = _as_int(self.query_one("#set-tps", Input).value,
                              "Equity TPS", minimum=1)
            strat_sym_val = self.query_one("#set-strat-sym", Input).value.upper()
            crypto_sym_val = self.query_one("#set-crypto-sym", Input).value
            spike_val = _as_float(self.query_one("#set-spike", Input).value,
                                  "Spike threshold", positive=True)
            snap_val = _as_float(self.query_one("#set-snapdrop", Input).value,
                                 "Snapdrop threshold", positive=True)
            if not strat_sym_val or not crypto_sym_val:
                raise ValueError("Strategy symbols must not be empty")
            bot_cfg = self._collect_bot()
        except ValueError as e:
            self._saving = False
            self.app.push_screen(ErrorScreen(f"Invalid input: {e}", id="errors"))
            return

        problems = validate_bot(bot_cfg)
        if problems:
            # Stay open: the offending fields are still on screen to be fixed.
            self._saving = False
            self.app.push_screen(ErrorScreen(
                "Bot settings rejected:\n" + "\n".join(f"· {p}" for p in problems),
                id="errors",
            ))
            return

        self._entropy._apply_settings(
            theme=theme_val, chart_type=chart_val, show_volume=vol_val,
            timeframe=tf_val, enable_equities=equities_val, enable_crypto=crypto_val,
            equity_source=equity_source_val, equity_tps=tps_val,
            strategy_symbol=strat_sym_val, crypto_strategy_symbol=crypto_sym_val,
            spike_pct=spike_val, snapdrop_pct=snap_val,
            chart_interval=chart_interval_val, chart_bars=chart_bars_val,
            show_depth=depth_val, bot=bot_cfg,
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
