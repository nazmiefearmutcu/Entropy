/**
 * Dev-only synthetic sidecar.
 *
 * `?mock=1` feeds the store a plausible SnapshotMessage and answers the REST
 * surface from memory, so the UI can be rendered and inspected without a
 * backend. Tests import `makeSnapshot` directly.
 */
import type {
  AppConfigJSON,
  AssetClass,
  BotConfigJSON,
  Candle,
  Leader,
  MetaResponse,
  SnapshotMessage,
  SymbolRow,
  WatchRow,
} from './contract'
import { SCHEMA_VERSION } from './contract'
import { setConnected, setConfig, setSnap } from './store'

function sym(ticker: string, name: string): SymbolRow {
  return {
    symbol: ticker, name, asset_class: 'equity', venue: 'us',
    ticker, exchange: 'US', base: '', quote: '', watched: false,
  }
}

function pair(
  venue: string, ticker: string, baseName: string, quoteName: string,
  base: string, quote: string,
): SymbolRow {
  return {
    symbol: `${venue}:${ticker}`, name: `${baseName} / ${quoteName}`,
    asset_class: 'crypto', venue, ticker,
    exchange: venue === 'binance-spot' ? 'BINANCE' : venue.toUpperCase(),
    base, quote, watched: false,
  }
}

// Shaped exactly like GET /api/symbols: canonical `venue:PAIR` ids, lowercase
// asset classes, and the split display fields. Inventing a tidier shape here
// (BTC/USDT, venue "NASDAQ") would let a contract mismatch pass unnoticed.
const UNIVERSE: SymbolRow[] = [
  sym('AAPL', 'Apple Inc.'),
  sym('MSFT', 'Microsoft Corp.'),
  sym('NVDA', 'NVIDIA Corp.'),
  sym('TSLA', 'Tesla Inc.'),
  sym('AMD', 'Advanced Micro Devices'),
  sym('SPY', 'SPDR S&P 500 ETF'),
  sym('DKNG', 'DraftKings Inc.'),
  pair('binance-spot', 'BTCUSDT', 'Bitcoin', 'TetherUS', 'BTC', 'USDT'),
  pair('binance-spot', 'ETHUSDT', 'Ethereum', 'TetherUS', 'ETH', 'USDT'),
  pair('binance-spot', 'SOLUSDT', 'Solana', 'TetherUS', 'SOL', 'USDT'),
  pair('coinbase', 'BTC-USD', 'Bitcoin', 'US Dollar', 'BTC', 'USD'),
]

const WATCHED = new Set(['AAPL', 'NVDA', 'binance-spot:BTCUSDT', 'SPY'])

function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 4294967296
  }
}

const BAR_SECONDS = 900
const BAR_COUNT = 180

function candles(seed: number, count: number, start: number, stepS: number): Candle[] {
  const r = rng(seed)
  const out: Candle[] = []
  let px = start
  // Bucket-aligned, computed once: real bars sit on fixed boundaries and do not
  // slide under the chart between frames.
  const t0 = Math.floor(Date.now() / 1000 / stepS) * stepS - (count - 1) * stepS
  for (let i = 0; i < count; i += 1) {
    const drift = (r() - 0.48) * start * 0.004
    const o = px
    const c = Math.max(0.01, o + drift)
    const h = Math.max(o, c) * (1 + r() * 0.0016)
    const l = Math.min(o, c) * (1 - r() * 0.0016)
    out.push([(t0 + i * stepS) * 1e9, o, h, l, c, Math.round(4000 + r() * 26000)])
    px = c
  }
  return out
}

let baseSeries: Candle[] | null = null

/** Stable history plus a live-moving last bar, the way a real feed behaves. */
function liveSeries(wobble: number): Candle[] {
  baseSeries ??= candles(11, BAR_COUNT, 228, BAR_SECONDS)
  const rows = baseSeries.slice()
  const i = rows.length - 1
  const [ts, o, h, l, c, v] = rows[i]
  const close = c + wobble
  rows[i] = [ts, o, Math.max(h, close), Math.min(l, close), close, v]
  return rows
}

function spark(seed: number, n = 22): number[] {
  const r = rng(seed)
  const out: number[] = []
  let v = 100
  for (let i = 0; i < n; i += 1) {
    v += (r() - 0.5) * 4
    out.push(v)
  }
  return out
}

function leaders(seed: number, sign: number): Leader[] {
  const r = rng(seed)
  return UNIVERSE.slice(0, 7).map((u, i) => [
    u.symbol,
    3 + Math.floor(r() * 40),
    40 + r() * 400,
    sign * (0.2 + r() * 3.4 + i * 0.05),
  ])
}

export const MOCK_APP_CONFIG: AppConfigJSON = {
  seed: 7,
  equity_tps: 12,
  enable_crypto: true,
  enable_equities: true,
  equity_source: 'yahoo',
  strategy_symbol: 'AAPL',
  crypto_strategy_symbol: 'BTC/USDT',
  theme: 'terminal',
  chart_type: 'candles',
  show_volume: true,
  show_depth: true,
  depth_bins: 12,
  depth_top_n: 6,
  risk_profile: 'medium',
  console_log_path: 'entropy_console.log',
  trade_csv_path: 'entropy_trades.csv',
  timeframe: '15m',
  chart_interval: '',
  chart_bars: 240,
  watchlist_path: 'watchlist.json',
}

export const MOCK_BOT_CONFIG: BotConfigJSON = {
  mode: 'paper',
  risk_profile: 'medium',
  risk_overrides: {
    per_trade_pct: null,
    max_concurrent: null,
    stop_loss_pct: null,
    take_profit_pct: null,
    max_total_exposure_pct: null,
    max_daily_loss_pct: null,
    cooldown_s: null,
    min_volatility_pct: null,
    vol_window_s: null,
  },
  strategies: ['consensus'],
  symbols: ['AAPL', 'BTC/USDT'],
  starting_cash: 100000,
  fee_bps: 2,
  slippage_bps: 1.5,
  ema_symbol: 'AAPL',
  ema_fast: 9,
  ema_slow: 21,
  momentum_min_pct: 0.15,
  seed: 7,
  equity_tps: 12,
  enable_crypto: true,
  enable_equities: true,
  console_log_path: 'entropy_console.log',
  trade_csv_path: 'entropy_trades.csv',
  live: { enabled: false, acknowledged_risk: false, exchange: 'binance', api_key: '', api_secret: '' },
  timeframe: '5m',
  bar_s: 0,
  warmup: true,
  consensus: {
    threshold: 0.55,
    min_bars: 40,
    vote_mode: 'adaptive',
    normalize: 'weights',
    min_participation: 0.5,
    w_ema: 1,
    w_macd: 1,
    w_rsi: 0.8,
    w_bollinger: 0.6,
    ema_fast: 9,
    ema_slow: 21,
    macd_fast: 12,
    macd_slow: 26,
    macd_signal: 9,
    rsi_period: 14,
    rsi_low: 30,
    rsi_high: 70,
    rsi_trend_low: 45,
    rsi_trend_high: 55,
    bb_period: 20,
    bb_std: 2,
    bb_low: 0.1,
    bb_high: 0.9,
    bb_trend_low: 0.25,
    bb_trend_high: 0.75,
    move_floor: 0.05,
    trend_er: 0.35,
    regime_window: 60,
    slope_lookback: 12,
    regime_tilt: 0.25,
    min_hold_bars: 3,
    cooldown_bars: 2,
    exit_mode: 'signal',
  },
}

export const MOCK_META: MetaResponse = {
  // These lists mirror GET /api/meta exactly. They used to be invented values
  // ('balanced', 'stop_only', 'yahoo'), which meant the mock could not catch a
  // contract mismatch — and it did hide one: the risk-profile select keyed on
  // the display name while BotConfig stores the lowercase key.
  timeframes: ['1m', '5m', '15m', '1h', '4h'],
  chart_intervals: ['1s', '5s', '15s', '30s', '1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d'],
  themes: ['entropy', 'dracula', 'cyberpunk', 'nord', 'forest', 'monochrome', 'sweet'],
  strategies: ['consensus', 'ema_cross', 'momentum_scalper', 'black_scholes'],
  risk_profiles: [
    {
      name: 'Frosty',
      color: 'cyan',
      description:
        'Frosty: allocates 1% of equity per trade, max 2 open positions, 0.5% stop / 2.0% target ' +
        '(4:1 ratio), up to 5% total exposure; halts all trading after a 2% daily loss with a 60s ' +
        'cooldown, and a 0.25% minimum volatility threshold measured over a 60s window.',
      per_trade_pct: 1.0,
      max_concurrent: 2,
      stop_loss_pct: 0.5,
      take_profit_pct: 2.0,
      max_total_exposure_pct: 5.0,
      max_daily_loss_pct: 2.0,
      cooldown_s: 60.0,
      min_volatility_pct: 0.25,
      vol_window_s: 60.0,
    },
    {
      name: 'Medium',
      color: 'yellow',
      description:
        'Medium: allocates 2.5% of equity per trade, up to 4 open positions, 1% stop / 2% target, ' +
        'up to 15% total exposure; halts all trading after a 5% daily loss with a 10s cooldown, ' +
        'and a 0.15% minimum volatility threshold measured over a 30s window.',
      per_trade_pct: 2.5,
      max_concurrent: 4,
      stop_loss_pct: 1.0,
      take_profit_pct: 2.0,
      max_total_exposure_pct: 15.0,
      max_daily_loss_pct: 5.0,
      cooldown_s: 10.0,
      min_volatility_pct: 0.15,
      vol_window_s: 30.0,
    },
    {
      name: 'Extreme',
      color: 'red',
      description:
        'Extreme: allocates 5% of equity per trade, up to 8 open positions, 2% stop / 4% target, ' +
        'up to 40% total exposure; halts all trading after a 10% daily loss with a 2s cooldown, ' +
        'and a 0.05% minimum volatility threshold measured over a 10s window.',
      per_trade_pct: 5.0,
      max_concurrent: 8,
      stop_loss_pct: 2.0,
      take_profit_pct: 4.0,
      max_total_exposure_pct: 40.0,
      max_daily_loss_pct: 10.0,
      cooldown_s: 2.0,
      min_volatility_pct: 0.05,
      vol_window_s: 10.0,
    },
  ],
  vote_modes: ['adaptive', 'trend', 'mean_revert', 'legacy'],
  normalize_modes: ['participating', 'total'],
  exit_modes: ['score', 'trend_flip', 'either'],
  equity_sources: ['sim', 'live', 'auto'],
  settings_path: '/Users/you/Library/Application Support/Entropy/settings.json',
}

/** Deterministic snapshot; `tick` nudges the prices so flashes are visible. */
export function makeSnapshot(tick = 0, overrides: Partial<SnapshotMessage> = {}): SnapshotMessage {
  const wobble = Math.sin(tick / 7) * 0.9
  const bars = liveSeries(wobble)
  const watchlist: WatchRow[] = [...WATCHED].map((symbol, i) => {
    const meta = UNIVERSE.find((u) => u.symbol === symbol)
    return [
      symbol,
      meta?.ticker ?? symbol,
      meta?.name ?? symbol,
      meta?.exchange ?? 'US',
      100 + i * 37 + wobble,
      (i % 2 === 0 ? 1 : -1) * (0.4 + i * 0.35 + wobble * 0.1),
      spark(3 + i),
    ]
  })

  return {
    type: 'snapshot',
    schema_version: SCHEMA_VERSION,
    ts_ns: Date.now() * 1e6,
    buy_pct: 54 + wobble * 3,
    sell_pct: 46 - wobble * 3,
    raw_hz: 118 + wobble * 6,
    accel: wobble > 0 ? 'expanding' : 'steady',
    new_highs: leaders(21, 1),
    new_lows: leaders(31, -1),
    ticker: [
      ['1m', UNIVERSE.slice(0, 4).map((u, i) => [u.symbol, 40 - i * 6] as [string, number])],
      ['5m', UNIVERSE.slice(2, 6).map((u, i) => [u.symbol, 120 - i * 14] as [string, number])],
      ['15m', UNIVERSE.slice(1, 5).map((u, i) => [u.symbol, 310 - i * 32] as [string, number])],
    ],
    focus: {
      symbol: 'AAPL',
      asset: 'EQUITY',
      last: bars[bars.length - 1][4] + wobble,
      pct: 0.92 + wobble * 0.2,
      hi: 231.4,
      lo: 226.1,
      candles: bars,
      depth: {
        basis: 'yahoo_1m_vap',
        is_synthetic: true,
        reference_price: 228.9,
        bids: [
          [228.8, 1420],
          [228.7, 980],
          [228.6, 2310],
          [228.5, 640],
          [228.4, 1180],
          [228.3, 520],
        ],
        asks: [
          [229.0, 1130],
          [229.1, 1890],
          [229.2, 740],
          [229.3, 2050],
          [229.4, 610],
          [229.5, 1320],
        ],
      },
      fundamentals: { pe: 31.4, market_cap: 3.42e12, high_52w: 260.1, low_52w: 164.08 },
      interval: '15m',
      timeframe: '15m',
    },
    watchlist,
    market_status: 'open',
    source: 'yahoo',
    settings: {
      timeframe: '15m',
      chart_interval: '',
      chart_type: 'candles',
      show_volume: true,
      show_depth: true,
      equity_source: 'yahoo',
      enable_equities: true,
      enable_crypto: true,
      theme: 'terminal',
    },
    feeds: { equities: 'live', crypto: 'sim', detail: 'yahoo 1m poll; crypto simulator' },
    bot: {
      running: true,
      paused: false,
      halted: false,
      warm: true,
      mode: 'paper',
      timeframe: '5m',
      bar_s: 0,
      risk_profile: 'medium',
      risk_description: 'Default. Moderate size with a hard daily loss cap.',
      ticks: 41230 + tick,
      cash: 62410.22,
      equity: 100412.55 + wobble * 40,
      realized_pnl: 318.4,
      unrealized_pnl: 94.15 + wobble * 12,
      daily_pnl: 412.55 + wobble * 40,
      open_count: 2,
      positions: [
        {
          symbol: 'AAPL',
          side: 'long',
          qty: 120,
          entry_px: 226.4,
          mark_px: 228.9 + wobble,
          unrealized_pnl: 300 + wobble * 12,
          stop_px: 222.3,
          tp_px: 233.6,
        },
        {
          symbol: 'BTC/USDT',
          side: 'short',
          qty: 0.35,
          entry_px: 63120,
          mark_px: 63410,
          unrealized_pnl: -101.5,
          stop_px: 64260,
          tp_px: 61180,
        },
      ],
      strategies: [
        {
          name: 'consensus',
          warm: true,
          regimes: { AAPL: 'trend', 'BTC/USDT': 'range' },
          directions: { AAPL: 1, 'BTC/USDT': -1 },
          sigmas: {},
          scores: {},
        },
        {
          name: 'black_scholes',
          warm: true,
          regimes: { 'BTC/USDT': 'crypto σ58%' },
          // Short, so the sign agrees with the negative score below — and so the
          // '+1' the BotDock test matches on stays unique to the consensus row.
          directions: { 'BTC/USDT': -1 },
          sigmas: { 'BTC/USDT': 0.5814 },
          scores: { 'BTC/USDT': -0.2137 },
        },
      ],
      last_signals: [
        '09:41:02 AAPL LONG score 0.71 regime=trend part=0.83',
        '09:38:44 BTC/USDT SHORT score -0.62 regime=range part=0.75',
        '09:31:10 AAPL exit signal score 0.12',
      ],
      last_rejects: [
        '09:44:51 NVDA rejected: cooldown_bars active (2 remaining)',
        '09:43:12 TSLA rejected: min_participation 0.38 < 0.50',
        '09:40:02 SPY rejected: volatility 0.09% below floor 0.15%',
      ],
    },
    ...overrides,
  }
}

/**
 * Copy only keys the target already declares. Mirrors the real sidecar's
 * "unknown fields are not accepted" rule and keeps the mock from growing
 * arbitrary attacker-shaped properties.
 */
function mergeKnown<T extends object>(target: T, patch: Record<string, unknown>) {
  for (const key of Object.keys(target)) {
    if (Object.hasOwn(patch, key)) {
      ;(target as Record<string, unknown>)[key] = patch[key]
    }
  }
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

/** Installs the fetch shim and starts pushing frames. Returns a stop function. */
export function startMock(): () => void {
  const app = { ...MOCK_APP_CONFIG }
  const bot = { ...MOCK_BOT_CONFIG, consensus: { ...MOCK_BOT_CONFIG.consensus } }
  const watched = new Set(WATCHED)
  let focused = 'AAPL'
  const real = globalThis.fetch.bind(globalThis)

  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!url.includes('/api/')) return real(input, init)
    const path = url.slice(url.indexOf('/api/'))

    if (path.startsWith('/api/meta')) return jsonResponse(MOCK_META)
    if (path.startsWith('/api/settings')) {
      if ((init?.method ?? 'GET') === 'GET') return jsonResponse({ app, bot })
      const patch = JSON.parse(String(init?.body ?? '{}')) as {
        app?: Record<string, unknown>
        bot?: Record<string, unknown>
      }
      mergeKnown(app, patch.app ?? {})
      const botPatch = patch.bot ?? {}
      const nested = new Set(['consensus', 'risk_overrides', 'live'])
      const flat: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(botPatch)) if (!nested.has(k)) flat[k] = v
      mergeKnown(bot, flat)
      if (botPatch.consensus && typeof botPatch.consensus === 'object') {
        mergeKnown(bot.consensus, botPatch.consensus as Record<string, unknown>)
      }
      if (botPatch.risk_overrides && typeof botPatch.risk_overrides === 'object') {
        mergeKnown(bot.risk_overrides, botPatch.risk_overrides as Record<string, unknown>)
      }
      if (botPatch.live && typeof botPatch.live === 'object') {
        mergeKnown(bot.live, botPatch.live as Record<string, unknown>)
      }
      return jsonResponse({ ok: true, message: 'saved (mock)', problems: [] })
    }
    if (path.startsWith('/api/symbols')) {
      const q = new URL(url, 'http://x').searchParams.get('q')?.toLowerCase() ?? ''
      const rows: SymbolRow[] = UNIVERSE.filter(
        (u) =>
          !q ||
          u.ticker.toLowerCase().includes(q) ||
          u.name.toLowerCase().includes(q) ||
          u.base.toLowerCase().startsWith(q),
      ).map((u) => ({ ...u, watched: watched.has(u.symbol) }))
      return jsonResponse(rows)
    }
    if (path.startsWith('/api/watchlist')) {
      const method = init?.method ?? 'GET'
      if (method === 'GET') {
        return jsonResponse(UNIVERSE.filter((u) => watched.has(u.symbol)))
      }
      if (method === 'POST') {
        const body = JSON.parse(String(init?.body ?? '{}')) as { symbol?: string }
        if (body.symbol) watched.add(body.symbol)
        return jsonResponse({ ok: true, message: 'watched (mock)', problems: [] })
      }
      const sym = decodeURIComponent(path.replace('/api/watchlist/', ''))
      watched.delete(sym)
      return jsonResponse({ ok: true, message: 'unwatched (mock)', problems: [] })
    }
    if (path.startsWith('/api/focus')) {
      const body = JSON.parse(String(init?.body ?? '{}')) as { symbol?: string }
      if (body.symbol) focused = body.symbol
      return jsonResponse({ ok: true, message: `focus ${focused} (mock)`, problems: [] })
    }
    return jsonResponse({ ok: true, message: 'ok (mock)', problems: [] })
  }

  /** Reflect everything the mock actually stores, so no control lies on screen. */
  function frame(tick: number): SnapshotMessage {
    const base = makeSnapshot(tick)
    const entry = UNIVERSE.find((u) => u.symbol === focused)
    const asset: AssetClass =
      entry?.asset_class === 'CRYPTO' ? 'CRYPTO' : entry?.asset_class === 'EQUITY' ? 'EQUITY' : 'SIM'
    return {
      ...base,
      focus: {
        ...base.focus,
        symbol: focused,
        asset,
        interval: app.chart_interval || app.timeframe,
        timeframe: app.timeframe,
        depth: app.show_depth ? base.focus.depth : null,
        fundamentals: asset === 'EQUITY' ? base.focus.fundamentals : null,
      },
      watchlist: base.watchlist.filter(([sym]) => watched.has(sym)),
      settings: {
        timeframe: app.timeframe,
        chart_interval: app.chart_interval,
        chart_type: app.chart_type,
        show_volume: app.show_volume,
        show_depth: app.show_depth,
        equity_source: app.equity_source,
        enable_equities: app.enable_equities,
        enable_crypto: app.enable_crypto,
        theme: app.theme,
      },
      bot: base.bot
        ? { ...base.bot, mode: bot.mode, timeframe: bot.timeframe, bar_s: bot.bar_s, risk_profile: bot.risk_profile }
        : null,
    }
  }

  setConfig({ meta: MOCK_META, settings: { app, bot }, error: null, loading: false })
  setConnected(true)
  let tick = 0
  const id = setInterval(() => {
    tick += 1
    setSnap(frame(tick))
  }, 200)
  setSnap(frame(0))

  return () => {
    clearInterval(id)
    globalThis.fetch = real
    setConnected(false)
  }
}

export function mockEnabled(): boolean {
  try {
    return new URLSearchParams(location.search).get('mock') === '1'
  } catch {
    return false
  }
}
