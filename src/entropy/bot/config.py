from __future__ import annotations

import msgspec

from entropy.engine.timeframe import TIMEFRAMES, get_timeframe
from entropy.quant.vol import RealizedVolSource, VolSource

from .risk.profiles import RiskProfile, get_profile, make_custom
from .strategies.base import Strategy
from .strategies.black_scholes import BlackScholesStrategy
from .strategies.consensus import ConsensusStrategy
from .strategies.ema_cross import EmaCrossStrategy
from .strategies.momentum_scalper import MomentumScalper

#: Strategy names ``build_strategies`` understands (also the UI's checkbox list).
STRATEGY_NAMES = ("consensus", "ema_cross", "momentum_scalper", "black_scholes")


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


class BlackScholesConfig(msgspec.Struct, frozen=True):
    """Every knob of :class:`~entropy.bot.strategies.black_scholes.BlackScholesStrategy`.

    Carry is a configured constant in both markets. Live funding would need a
    crocodile ``Catalog`` — ``funding_apr`` reads a stored ``funding`` channel —
    and the bot ingests none, so ``crypto_carry_source="funding"`` is reserved
    for a future catalog-backed run rather than pretending to data that is not
    there.
    """

    # --- signal ----------------------------------------------------------
    horizon_bars: int = 30
    barrier_k: float = 1.0
    threshold: float = 0.15
    min_bars: int = 40
    # --- volatility ------------------------------------------------------
    vol_lambda: float = 0.94
    vol_floor: float = 0.05
    vol_source: str = "realized"          # realized | chain
    # --- drift -----------------------------------------------------------
    drift_window: int = 20
    drift_shrinkage: float = 0.5
    drift_cap_sigmas: float = 3.0
    # --- risk translation ------------------------------------------------
    z_stop: float = 1.0
    z_tp: float = 2.0
    stop_floor_pct: float = 0.05
    risk_budget_pct: float = 1.0
    # --- carry -----------------------------------------------------------
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0
    crypto_carry_apr: float = 0.0
    crypto_carry_source: str = "constant"  # constant ("funding" reserved; validate rejects it)
    # --- position lifecycle ----------------------------------------------
    min_hold_bars: int = 3
    cooldown_bars: int = 2
    exit_mode: str = "score"               # score | flip | either


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
    fee_bps: float = 1.0
    slippage_bps: float = 1.0
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
    black_scholes: BlackScholesConfig = msgspec.field(default_factory=BlackScholesConfig)

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
    if cfg.fee_bps < 0.0 or cfg.slippage_bps < 0.0:
        problems.append("fee/slippage must be >= 0 bps")
    if not cfg.strategies:
        problems.append("select at least one strategy")
    for name in cfg.strategies:
        if name not in STRATEGY_NAMES:
            problems.append(f"unknown strategy {name!r}")
    if not cfg.ema_symbol:
        problems.append("EMA-cross symbol must not be empty")
    try:
        cfg.profile()
    except (KeyError, TypeError) as exc:
        problems.append(f"risk profile: {exc}")
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
    b = cfg.black_scholes
    if b.horizon_bars < 1:
        problems.append("black-scholes horizon must be at least 1 bar")
    if not 0.0 < b.threshold <= 1.0:
        problems.append("black-scholes threshold must be in (0, 1]")
    if not 0.0 < b.vol_lambda < 1.0:
        problems.append("black-scholes vol lambda must be in (0, 1)")
    if b.vol_floor <= 0.0:
        problems.append("black-scholes vol floor must be positive")
    if b.barrier_k <= 0.0:
        problems.append("black-scholes barrier width must be positive")
    if b.drift_window < 2:
        problems.append("black-scholes drift window must be at least 2 bars")
    if b.min_bars < b.drift_window + 1:
        problems.append(
            "black-scholes min bars must exceed the drift window by at least 1"
        )
    if not 0.0 <= b.drift_shrinkage <= 1.0:
        problems.append("black-scholes drift shrinkage must be a fraction in [0, 1]")
    if b.drift_cap_sigmas <= 0.0:
        problems.append("black-scholes drift cap must be positive")
    if b.z_stop <= 0.0:
        problems.append("black-scholes z_stop must be positive")
    if b.z_tp <= 0.0:
        problems.append("black-scholes z_tp must be positive")
    if not 0.0 < b.stop_floor_pct < 50.0:
        problems.append("black-scholes stop floor must be in (0, 50) percent")
    if not 0.0 < b.risk_budget_pct <= 100.0:
        problems.append("black-scholes risk budget must be in (0, 100] percent")
    if b.vol_source not in ("realized", "chain"):
        problems.append(f"unknown black-scholes vol source {b.vol_source!r}")
    if b.crypto_carry_source == "funding":
        # Refused rather than accepted-and-ignored, for the same reason
        # `_build_vol_source` refuses to downgrade "chain" to realized vol: a run
        # whose ledger says "funding" must not have traded on a constant. Live
        # funding needs a crocodile Catalog the bot does not ingest, so the
        # option stays reserved until something can actually read it.
        problems.append(
            "black-scholes carry source 'funding' is not supported yet: live "
            "funding needs a crocodile catalog the bot does not ingest, so "
            "'constant' is the only carry source a run can honestly claim"
        )
    elif b.crypto_carry_source != "constant":
        problems.append(
            f"unknown black-scholes carry source {b.crypto_carry_source!r}"
        )
    if b.exit_mode not in ("score", "flip", "either"):
        problems.append(f"unknown black-scholes exit mode {b.exit_mode!r}")
    if cfg.mode == "live" and not cfg.live.acknowledged_risk:
        problems.append("live mode requires the risk acknowledgement")
    return problems


def _build_vol_source(cfg: BlackScholesConfig) -> VolSource:
    """Realized vol by default; the chain source is opt-in and crypto-only.

    ``ChainVolSource`` needs a crocodile ``Catalog`` the bot does not build, so
    it will refuse — visibly, per symbol — rather than quietly becoming realized
    vol. That refusal is the point: a run whose ledger says "chain" must not have
    traded on something else.
    """
    if cfg.vol_source == "chain":
        from entropy.quant.vol import ChainVolSource

        return ChainVolSource()
    return RealizedVolSource(lam=cfg.vol_lambda, floor=cfg.vol_floor)


def build_strategies(cfg: BotConfig) -> list[Strategy]:
    syms = cfg.symbols or None
    bar_s = cfg.bar_seconds()
    c = cfg.consensus
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
            ))
        elif name == "momentum_scalper":
            out.append(MomentumScalper(symbols=syms, min_pct=cfg.momentum_min_pct))
        elif name == "ema_cross":
            out.append(
                EmaCrossStrategy(symbol=cfg.ema_symbol, fast=cfg.ema_fast, slow=cfg.ema_slow)
            )
        elif name == "black_scholes":
            b = cfg.black_scholes
            out.append(BlackScholesStrategy(
                symbols=syms, bar_s=bar_s,
                horizon_bars=b.horizon_bars, barrier_k=b.barrier_k,
                threshold=b.threshold, min_bars=b.min_bars,
                drift_window=b.drift_window, drift_shrinkage=b.drift_shrinkage,
                drift_cap_sigmas=b.drift_cap_sigmas,
                z_stop=b.z_stop, z_tp=b.z_tp, stop_floor_pct=b.stop_floor_pct,
                risk_budget_pct=b.risk_budget_pct,
                max_size_pct=cfg.profile().per_trade_pct,
                risk_free_rate=b.risk_free_rate, dividend_yield=b.dividend_yield,
                crypto_carry_apr=b.crypto_carry_apr,
                min_hold_bars=b.min_hold_bars, cooldown_bars=b.cooldown_bars,
                exit_mode=b.exit_mode,
                vol_source=_build_vol_source(b),
                warmup_symbol=cfg.ema_symbol,
            ))
        else:
            raise KeyError(f"Unknown strategy {name!r}")
    return out
