/**
 * Entropy sidecar wire contract.
 *
 * This module is the single typed source of truth for everything that crosses
 * the HTTP/WebSocket boundary. Schema version 2.
 */

export const SCHEMA_VERSION = 2

/* ------------------------------------------------------------------ *
 * Live snapshot (ws://127.0.0.1:{port}/ws/live, ~10 Hz)
 * ------------------------------------------------------------------ */

export type Leader = [symbol: string, count: number, price: number, pct: number]
export type TickerEntry = [window: string, entries: [string, number][]]
export type Candle = [tsNs: number, o: number, h: number, l: number, c: number, v: number]
export type Level = [price: number, size: number]
// The canonical id stays first because everything keys off it, but the row now
// also carries what a human should actually read: the exchange's own ticker,
// the instrument name and the venue badge. Rendering the canonical id alone
// clipped every crypto row to "binance-s…".
export type WatchRow = [
  symbol: string,
  ticker: string,
  name: string,
  exchange: string,
  last: number | null,
  pct: number | null,
  spark: number[],
]

export type AssetClass = 'EQUITY' | 'CRYPTO' | 'SIM'

/** Feed lifecycle states reported per venue class. */
export type FeedState = 'off' | 'sim' | 'live' | 'connecting' | 'error'

export interface DepthLevels {
  basis: string
  is_synthetic: boolean
  reference_price: number
  bids: Level[]
  asks: Level[]
}

export interface Fundamentals {
  pe: number | null
  market_cap: number | null
  high_52w: number | null
  low_52w: number | null
}

export interface FocusView {
  symbol: string
  asset: AssetClass
  last: number | null
  pct: number | null
  hi: number | null
  lo: number | null
  candles: Candle[]
  depth: DepthLevels | null
  fundamentals: Fundamentals | null
  /** Resolved chart interval actually used to bucket `candles`, e.g. "1m". */
  interval: string
  /** Scanner timeframe currently driving the high/low windows, e.g. "15m". */
  timeframe: string
}

/** The nine settings echoed on every frame so the UI never has to guess. */
export interface SettingsView {
  timeframe: string
  chart_interval: string
  chart_type: string
  show_volume: boolean
  show_depth: boolean
  equity_source: string
  enable_equities: boolean
  enable_crypto: boolean
  theme: string
}

export interface FeedStatus {
  equities: string
  crypto: string
  detail: string
}

export interface BotPosition {
  symbol: string
  side: string
  qty: number
  entry_px: number
  mark_px: number
  unrealized_pnl: number
  stop_px: number
  tp_px: number
}

export interface BotStrategy {
  name: string
  warm: boolean
  /** symbol -> regime label ("trend" | "range" | "chop" | …) */
  regimes: Record<string, string>
  /** symbol -> directional vote in {-1, 0, +1} */
  directions: Record<string, number>
}

export interface BotView {
  running: boolean
  paused: boolean
  halted: boolean
  warm: boolean
  mode: string
  timeframe: string
  bar_s: number
  risk_profile: string
  risk_description: string
  ticks: number
  cash: number
  equity: number
  realized_pnl: number
  unrealized_pnl: number
  daily_pnl: number
  open_count: number
  positions: BotPosition[]
  strategies: BotStrategy[]
  last_signals: string[]
  last_rejects: string[]
}

export interface SnapshotMessage {
  type: 'snapshot'
  schema_version: number
  ts_ns: number
  buy_pct: number
  sell_pct: number
  raw_hz: number
  accel: string
  new_highs: Leader[]
  new_lows: Leader[]
  ticker: TickerEntry[]
  focus: FocusView
  watchlist: WatchRow[]
  market_status: string
  source: string
  settings: SettingsView
  feeds: FeedStatus
  bot: BotView | null
}

/* ------------------------------------------------------------------ *
 * REST
 * ------------------------------------------------------------------ */

/** Uniform mutation acknowledgement. `problems` is human-readable. */
export interface ApiAck {
  ok: boolean
  message: string
  problems: string[]
}

export interface RiskProfileMeta {
  name: string
  description: string
  /** Textual colour name supplied by the sidecar; advisory only. */
  color?: string
  per_trade_pct: number
  max_concurrent: number
  stop_loss_pct: number
  take_profit_pct: number
  max_total_exposure_pct: number
  max_daily_loss_pct: number
  cooldown_s: number
  min_volatility_pct: number
  vol_window_s: number
}

export interface MetaResponse {
  timeframes: string[]
  chart_intervals: string[]
  themes: string[]
  strategies: string[]
  risk_profiles: RiskProfileMeta[]
  vote_modes: string[]
  normalize_modes: string[]
  exit_modes: string[]
  equity_sources: string[]
  settings_path: string
}

export interface SymbolRow {
  /** Canonical feed id — "AAPL", "binance-spot:BTCUSDT". Never shown raw. */
  symbol: string
  /** What the instrument IS: "Apple Inc.", "Bitcoin / TetherUS". */
  name: string
  asset_class: string
  venue: string
  /** The exchange's own symbol: "AAPL", "BTCUSDT", "BTC-USD". */
  ticker: string
  /** Badge text: "US", "BINANCE", "COINBASE". */
  exchange: string
  /** Crypto only. */
  base: string
  quote: string
  watched: boolean
}

export interface WatchlistEntry {
  symbol: string
  name: string
  asset_class: string
  venue: string
  ticker: string
  exchange: string
  base: string
  quote: string
}

/* ---- persisted configuration ------------------------------------- */

export interface AppConfigJSON {
  seed: number
  equity_tps: number
  enable_crypto: boolean
  enable_equities: boolean
  equity_source: string
  strategy_symbol: string
  crypto_strategy_symbol: string
  theme: string
  chart_type: string
  show_volume: boolean
  show_depth: boolean
  depth_bins: number
  depth_top_n: number
  risk_profile: string
  console_log_path: string
  trade_csv_path: string
  timeframe: string
  /** "" means: follow the scanner timeframe. */
  chart_interval: string
  chart_bars: number
  watchlist_path: string
  /**
   * Engine sub-config owned by the sidecar. Read-only here: the PUT deep-merges,
   * so omitting the key preserves it, and sending a partial would be rejected.
   */
  engine?: Record<string, unknown>
}

export interface RiskOverridesJSON {
  per_trade_pct: number | null
  max_concurrent: number | null
  stop_loss_pct: number | null
  take_profit_pct: number | null
  max_total_exposure_pct: number | null
  max_daily_loss_pct: number | null
  cooldown_s: number | null
  min_volatility_pct: number | null
  vol_window_s: number | null
}

export interface ConsensusConfigJSON {
  threshold: number
  min_bars: number
  vote_mode: string
  normalize: string
  min_participation: number
  w_ema: number
  w_macd: number
  w_rsi: number
  w_bollinger: number
  ema_fast: number
  ema_slow: number
  macd_fast: number
  macd_slow: number
  macd_signal: number
  rsi_period: number
  rsi_low: number
  rsi_high: number
  rsi_trend_low: number
  rsi_trend_high: number
  bb_period: number
  bb_std: number
  bb_low: number
  bb_high: number
  bb_trend_low: number
  bb_trend_high: number
  move_floor: number
  trend_er: number
  regime_window: number
  slope_lookback: number
  regime_tilt: number
  min_hold_bars: number
  cooldown_bars: number
  exit_mode: string
}

export interface BotLiveJSON {
  enabled: boolean
  acknowledged_risk: boolean
  exchange: string
  api_key: string
  api_secret: string
}

export interface BotConfigJSON {
  mode: string
  risk_profile: string
  risk_overrides: RiskOverridesJSON
  strategies: string[]
  symbols: string[]
  starting_cash: number
  fee_bps: number
  slippage_bps: number
  ema_symbol: string
  ema_fast: number
  ema_slow: number
  momentum_min_pct: number
  seed: number
  equity_tps: number
  enable_crypto: boolean
  enable_equities: boolean
  console_log_path: string
  trade_csv_path: string
  live: BotLiveJSON
  timeframe: string
  /** 0 = follow `timeframe`; otherwise an explicit bar width in seconds. */
  bar_s: number
  /** Python bool. Sending a number here is rejected by the sidecar. */
  warmup: boolean
  consensus: ConsensusConfigJSON
}

export interface SettingsPayload {
  app: AppConfigJSON
  bot: BotConfigJSON
}

/**
 * `PUT /api/settings` deep-merges, so nested partials are legal — but unknown
 * keys are rejected outright rather than ignored, and validation is all-or-
 * nothing across both halves.
 */
export interface SettingsPatch {
  app?: Partial<Omit<AppConfigJSON, 'engine'>>
  bot?: Partial<Omit<BotConfigJSON, 'consensus' | 'risk_overrides' | 'live'>> & {
    consensus?: Partial<ConsensusConfigJSON>
    risk_overrides?: Partial<RiskOverridesJSON>
    live?: Partial<BotLiveJSON>
  }
}

export type BotAction = 'start' | 'stop' | 'pause' | 'resume' | 'halt'
