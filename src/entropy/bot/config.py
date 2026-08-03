from __future__ import annotations

import math

import msgspec

from entropy.engine.timeframe import TIMEFRAMES, get_timeframe

from .costs import CostModel, MarketClass, MarketCosts
from .risk.profiles import RiskProfile, get_profile, make_custom
from .strategies.base import Strategy
from .strategies.consensus import ConsensusStrategy
from .strategies.ema_cross import EmaCrossStrategy
from .strategies.momentum_scalper import MomentumScalper

#: Strategy names ``build_strategies`` understands (also the UI's checkbox list).
STRATEGY_NAMES = ("consensus", "ema_cross", "momentum_scalper")


class LiveConfig(msgspec.Struct, frozen=True):
    enabled: bool = False
    acknowledged_risk: bool = False
    exchange: str = "binance"
    api_key: str = ""
    api_secret: str = ""


class ConsensusConfig(msgspec.Struct, frozen=True):
    """Every knob of :class:`~entropy.bot.strategies.consensus.ConsensusStrategy`.

    Previously all of these were hardcoded inside the strategy, so the only way
    to change how the bot thought was to edit the source. They live here so the
    TUI/GUI settings panels can expose them and so a run's parameters are
    recorded alongside its ledger.
    """

    # --- scoring ---------------------------------------------------------
    threshold: float = 0.5
    min_bars: int = 35
    vote_mode: str = "adaptive"          # adaptive | trend | mean_revert | legacy
    normalize: str = "participating"     # participating | total
    min_participation: float = 0.5
    w_ema: float = 0.35
    w_macd: float = 0.30
    w_rsi: float = 0.20
    w_bollinger: float = 0.15
    # --- indicators ------------------------------------------------------
    ema_fast: int = 9
    ema_slow: int = 21
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    rsi_period: int = 14
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    rsi_trend_low: float = 45.0
    rsi_trend_high: float = 55.0
    bb_period: int = 20
    bb_std: float = 2.0
    bb_low: float = 0.05
    bb_high: float = 0.95
    bb_trend_low: float = 0.20
    bb_trend_high: float = 0.80
    # --- regime ----------------------------------------------------------
    move_floor: float = 0.0005
    trend_er: float = 0.35
    regime_window: int = 20
    slope_lookback: int = 5
    regime_tilt: float = 2.0
    # --- position lifecycle ----------------------------------------------
    min_hold_bars: int = 3
    cooldown_bars: int = 2
    exit_mode: str = "score"             # score | trend_flip | either

    def weights(self) -> dict[str, float]:
        return {
            "ema": self.w_ema, "macd": self.w_macd,
            "rsi": self.w_rsi, "bollinger": self.w_bollinger,
        }


class MarketCostConfig(msgspec.Struct, frozen=True):
    """Per-market fee/slippage overrides (bps per side); ``None`` = flat fallback.

    Defaults are the researched 2026-08-02 taker schedules of the largest
    venues: Binance spot 10.0 bps (7.5 with the BNB discount — not assumed),
    Binance USDT-M futures 5.0 bps (4.5 with BNB), US equities via
    Interactive Brokers tiered ~2.0 bps. Slippage is a model input for the
    liquid symbols the bot feeds on.
    """

    equity_fee_bps: float | None = 2.0
    equity_slippage_bps: float | None = 2.0
    crypto_spot_fee_bps: float | None = 10.0
    crypto_spot_slippage_bps: float | None = 3.0
    crypto_futures_fee_bps: float | None = 5.0
    crypto_futures_slippage_bps: float | None = 2.0

    @classmethod
    def flat(cls) -> MarketCostConfig:
        """Every field ``None``: all markets inherit the flat fee/slippage."""
        return cls(
            equity_fee_bps=None, equity_slippage_bps=None,
            crypto_spot_fee_bps=None, crypto_spot_slippage_bps=None,
            crypto_futures_fee_bps=None, crypto_futures_slippage_bps=None,
        )

    def as_mapping(self) -> dict[MarketClass, MarketCosts]:
        """Non-None fields as a :class:`CostModel` market map."""
        out: dict[MarketClass, MarketCosts] = {}
        if self.equity_fee_bps is not None or self.equity_slippage_bps is not None:
            out[MarketClass.EQUITY] = MarketCosts(
                fee_bps=self.equity_fee_bps,
                slippage_bps=self.equity_slippage_bps,
            )
        if self.crypto_spot_fee_bps is not None or self.crypto_spot_slippage_bps is not None:
            out[MarketClass.CRYPTO_SPOT] = MarketCosts(
                fee_bps=self.crypto_spot_fee_bps,
                slippage_bps=self.crypto_spot_slippage_bps,
            )
        if self.crypto_futures_fee_bps is not None or self.crypto_futures_slippage_bps is not None:
            out[MarketClass.CRYPTO_FUTURES] = MarketCosts(
                fee_bps=self.crypto_futures_fee_bps,
                slippage_bps=self.crypto_futures_slippage_bps,
            )
        return out


class RiskOverrides(msgspec.Struct, frozen=True):
    """Optional per-field overrides applied on top of the named risk preset.

    ``None`` means "inherit the preset". This is how the settings UI offers a
    Custom profile without needing a whole extra preset vocabulary.
    """

    per_trade_pct: float | None = None
    max_concurrent: int | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    max_total_exposure_pct: float | None = None
    max_daily_loss_pct: float | None = None
    cooldown_s: float | None = None
    min_volatility_pct: float | None = None
    vol_window_s: float | None = None

    def active(self) -> dict[str, object]:
        return {
            name: value
            for name, value in ((n, getattr(self, n)) for n in self.__struct_fields__)
            if value is not None
        }


class BotConfig(msgspec.Struct, frozen=True):
    mode: str = "paper"  # "paper" | "live"
    risk_profile: str = "medium"
    risk_overrides: RiskOverrides = msgspec.field(default_factory=RiskOverrides)
    strategies: tuple[str, ...] = ("consensus", "ema_cross")
    symbols: tuple[str, ...] = ()  # () = all symbols from the feed
    starting_cash: float = 100_000.0
    #: Flat fallback costs; overridden per market by `market_costs`.
    fee_bps: float = 1.0
    slippage_bps: float = 1.0
    #: Master switch for the cost-aware layer. False = every gate is off and
    #: only the flat fee/slippage apply — the pre-cost legacy behavior.
    cost_aware: bool = True
    #: Per-market fee/slippage overrides (realistic venue schedules by default).
    market_costs: MarketCostConfig = msgspec.field(default_factory=MarketCostConfig)
    #: ``k`` in the cost gates ``mean|r| >= k*C`` and ``0.798*sigma >= k*C``.
    cost_edge_mult: float = 2.0
    #: Churn red flag: reject entries whose round-trip cost is more than this
    #: fraction of the stop distance (``C/stop > max_cost_to_stop``).
    max_cost_to_stop: float = 0.5
    ema_symbol: str = "SPY"  # deterministic sim symbol by default; use "binance-spot:BTCUSDT" live
    ema_fast: int = 9
    ema_slow: int = 21
    momentum_min_pct: float = 0.15
    seed: int = 42
    equity_tps: int = 4000
    enable_crypto: bool = True
    enable_equities: bool = True
    console_log_path: str = "entropy_console.log"
    trade_csv_path: str = "entropy_trades.csv"
    live: LiveConfig = msgspec.field(default_factory=LiveConfig)

    # --- cadence ---------------------------------------------------------
    #: Drives the bot's scanner windows AND (unless `bar_s` overrides it) the
    #: length of the bar the strategies reason on. Previously the runner built a
    #: bare ``Engine()`` — permanently pinned to the legacy 30s/1m/5m windows
    #: with no way to ask for anything else.
    timeframe: str = "1m"
    #: Strategy bar length in seconds; 0.0 = derive it from `timeframe`.
    bar_s: float = 0.0
    #: Fetch history at startup so the strategies are warm instead of waiting
    #: `min_bars` live bars (35 minutes on a 1m cadence) before the first signal.
    warmup: bool = True

    consensus: ConsensusConfig = msgspec.field(default_factory=ConsensusConfig)

    def profile(self) -> RiskProfile:
        base = get_profile(self.risk_profile)
        overrides = self.risk_overrides.active()
        if not overrides:
            return base
        return msgspec.structs.replace(make_custom(**overrides), color=base.color)

    def bar_seconds(self) -> float:
        """Strategy bar length: the explicit override, else the timeframe's bar."""
        if self.bar_s > 0.0:
            return self.bar_s
        return get_timeframe(self.timeframe).bar_ns / 1_000_000_000

    def cost_model(self) -> CostModel | None:
        """Cost model for the paper executor, strategies and risk layer.

        ``None`` when ``cost_aware`` is off — the exact legacy wiring (gates
        are no-ops, flat fees apply), so old flat-fee runs reproduce
        byte-for-byte.
        """
        if not self.cost_aware:
            return None
        return CostModel(
            flat_fee_bps=self.fee_bps,
            flat_slippage_bps=self.slippage_bps,
            market=self.market_costs.as_mapping(),
        )


def validate(cfg: BotConfig) -> list[str]:
    """Human-readable problems with ``cfg``; an empty list means usable.

    The settings UIs call this before applying, so a bad number is reported as a
    sentence instead of surfacing later as a stack trace from inside a feed.
    """
    problems: list[str] = []
    if cfg.timeframe not in TIMEFRAMES:
        problems.append(f"unknown timeframe {cfg.timeframe!r}")
    if cfg.bar_s < 0.0:
        problems.append("bar length must be >= 0 (0 = follow the timeframe)")
    if cfg.starting_cash <= 0.0:
        problems.append("starting cash must be positive")
    if not math.isfinite(cfg.fee_bps) or not math.isfinite(cfg.slippage_bps):
        problems.append("fee/slippage must be finite numbers")
    elif cfg.fee_bps < 0.0 or cfg.slippage_bps < 0.0:
        problems.append("fee/slippage must be >= 0 bps")
    mc = cfg.market_costs
    for label, value in (
        ("equity fee", mc.equity_fee_bps), ("equity slippage", mc.equity_slippage_bps),
        ("crypto spot fee", mc.crypto_spot_fee_bps),
        ("crypto spot slippage", mc.crypto_spot_slippage_bps),
        ("crypto futures fee", mc.crypto_futures_fee_bps),
        ("crypto futures slippage", mc.crypto_futures_slippage_bps),
    ):
        if value is not None and not math.isfinite(value):
            problems.append(f"{label} must be a finite number")
        elif value is not None and value < 0.0:
            problems.append(f"{label} must be >= 0 bps")
    if not math.isfinite(cfg.cost_edge_mult):
        problems.append("cost edge multiplier must be a finite number")
    elif cfg.cost_edge_mult <= 0.0:
        problems.append("cost edge multiplier must be positive")
    if not math.isfinite(cfg.max_cost_to_stop):
        problems.append("max cost-to-stop must be a finite number")
    elif not 0.0 < cfg.max_cost_to_stop <= 1.0:
        problems.append("max cost-to-stop must be in (0, 1]")
    if not cfg.strategies:
        problems.append("select at least one strategy")
    for name in cfg.strategies:
        if name not in STRATEGY_NAMES:
            problems.append(f"unknown strategy {name!r}")
    if not cfg.ema_symbol:
        problems.append("EMA-cross symbol must not be empty")
    try:
        profile = cfg.profile()
    except (KeyError, TypeError) as exc:
        problems.append(f"risk profile: {exc}")
        profile = None
    if profile is not None:
        model = cfg.cost_model()
        if model is not None:
            # Cost-aware silent death: a stop so tight that the most expensive
            # active market's round trip exceeds max_cost_to_stop makes the
            # risk layer reject every entry (e.g. Frosty 0.5% + crypto spot
            # 26 bps -> 0.52 > 0.5). Surface it before the bot goes quiet.
            stop_bps = profile.stop_loss_pct * 100.0
            if stop_bps > 0.0:
                active: list[tuple[str, MarketClass]] = []
                if cfg.enable_equities:
                    active.append(("equities", MarketClass.EQUITY))
                if cfg.enable_crypto:
                    active.append(("crypto spot", MarketClass.CRYPTO_SPOT))
                    active.append(("crypto futures", MarketClass.CRYPTO_FUTURES))
                for label, cls in active:
                    costs = model.market.get(cls)
                    if costs is None:
                        costs = MarketCosts(
                            model.flat_fee_bps, model.flat_slippage_bps
                        )
                    else:
                        costs = costs.resolved(
                            model.flat_fee_bps, model.flat_slippage_bps
                        )
                    ratio = costs.round_trip_bps / stop_bps
                    if ratio > cfg.max_cost_to_stop:
                        problems.append(
                            f"{profile.name} stop {profile.stop_loss_pct:g}%: "
                            f"{label} round trip {costs.round_trip_bps:g} bps is "
                            f"{ratio:.2f}x the stop distance, above "
                            f"max_cost_to_stop={cfg.max_cost_to_stop:g} - cost-aware "
                            "entries would all be rejected"
                        )
    c = cfg.consensus
    if not 0.0 < c.threshold <= 1.0:
        problems.append("consensus threshold must be in (0, 1]")
    if c.ema_fast >= c.ema_slow:
        problems.append("consensus EMA fast period must be shorter than slow")
    if c.macd_fast >= c.macd_slow:
        problems.append("consensus MACD fast period must be shorter than slow")
    if min(c.ema_fast, c.macd_fast, c.rsi_period, c.bb_period) < 1:
        problems.append("consensus indicator periods must be >= 1")
    if sum(abs(w) for w in c.weights().values()) <= 0.0:
        problems.append("consensus weights cannot all be zero")
    if not 0.0 <= c.min_participation <= 1.0:
        problems.append("consensus min participation must be a fraction in [0, 1]")
    if cfg.mode == "live" and not cfg.live.acknowledged_risk:
        problems.append("live mode requires the risk acknowledgement")
    return problems


def build_strategies(cfg: BotConfig) -> list[Strategy]:
    syms = cfg.symbols or None
    bar_s = cfg.bar_seconds()
    c = cfg.consensus
    costs = cfg.cost_model()
    out: list[Strategy] = []
    for name in cfg.strategies:
        if name == "consensus":
            out.append(ConsensusStrategy(
                symbols=syms, bar_s=bar_s, threshold=c.threshold, min_bars=c.min_bars,
                weights=c.weights(), move_floor=c.move_floor, trend_er=c.trend_er,
                ema_fast=c.ema_fast, ema_slow=c.ema_slow,
                macd_fast=c.macd_fast, macd_slow=c.macd_slow, macd_signal=c.macd_signal,
                rsi_period=c.rsi_period, rsi_low=c.rsi_low, rsi_high=c.rsi_high,
                rsi_trend_low=c.rsi_trend_low, rsi_trend_high=c.rsi_trend_high,
                bb_period=c.bb_period, bb_std=c.bb_std,
                bb_low=c.bb_low, bb_high=c.bb_high,
                bb_trend_low=c.bb_trend_low, bb_trend_high=c.bb_trend_high,
                vote_mode=c.vote_mode, normalize=c.normalize,
                min_participation=c.min_participation,
                min_hold_bars=c.min_hold_bars, cooldown_bars=c.cooldown_bars,
                exit_mode=c.exit_mode, regime_window=c.regime_window,
                slope_lookback=c.slope_lookback, regime_tilt=c.regime_tilt,
                warmup_symbol=cfg.ema_symbol,
                costs=costs, cost_edge_mult=cfg.cost_edge_mult,
            ))
        elif name == "momentum_scalper":
            out.append(MomentumScalper(symbols=syms, min_pct=cfg.momentum_min_pct,
                                       costs=costs, cost_edge_mult=cfg.cost_edge_mult))
        elif name == "ema_cross":
            out.append(
                EmaCrossStrategy(symbol=cfg.ema_symbol, fast=cfg.ema_fast, slow=cfg.ema_slow)
            )
        else:
            raise KeyError(f"Unknown strategy {name!r}")
    return out
