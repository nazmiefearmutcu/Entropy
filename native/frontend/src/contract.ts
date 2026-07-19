export type Leader = [symbol: string, count: number, price: number, pct: number]
export type TickerEntry = [window: string, entries: [string, number][]]
export type Candle = [tsNs: number, o: number, h: number, l: number, c: number, v: number]
export type Level = [price: number, size: number]

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
  asset: 'EQUITY' | 'CRYPTO' | 'SIM'
  last: number | null
  pct: number | null
  hi: number | null
  lo: number | null
  candles: Candle[]
  depth: DepthLevels | null
  fundamentals: Fundamentals | null
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
  watchlist: [string, number | null, number | null, number[]][]
  market_status: string
  source: string
}
