/**
 * Snapshot store.
 *
 * Frames land at ~10 Hz, so nothing reads the whole snapshot. Components pull a
 * narrow slice through `useStoreValue(selector, equality)`; the cached value is
 * only replaced when the equality check says it actually moved, which keeps a
 * settings drawer or a bot table from re-rendering on every tick.
 */
import { useCallback, useRef, useSyncExternalStore } from 'react'
import type {
  BotView,
  Candle,
  DepthLevels,
  FeedStatus,
  Fundamentals,
  Leader,
  MetaResponse,
  SettingsPayload,
  SettingsView,
  SnapshotMessage,
  TickerEntry,
  WatchRow,
} from './contract'

let snap: SnapshotMessage | null = null
let connected = false
let frames = 0
const subs = new Set<() => void>()

function emit() {
  for (const f of subs) f()
}

export function subscribe(cb: () => void) {
  subs.add(cb)
  return () => {
    subs.delete(cb)
  }
}

export function setSnap(m: SnapshotMessage) {
  snap = m
  frames += 1
  emit()
}

export function setConnected(c: boolean) {
  if (c !== connected) {
    connected = c
    emit()
  }
}

export function getSnap(): SnapshotMessage | null {
  return snap
}

/** Test/mock helper: wipe all live state. */
export function resetStore() {
  snap = null
  connected = false
  frames = 0
  emit()
}

/* ------------------------------------------------------------ equality */

export function shallowEqual<T extends object | null>(a: T, b: T): boolean {
  if (Object.is(a, b)) return true
  if (a == null || b == null) return false
  const ra = a as Record<string, unknown>
  const rb = b as Record<string, unknown>
  const ak = Object.keys(ra)
  if (ak.length !== Object.keys(rb).length) return false
  for (const k of ak) if (!Object.is(ra[k], rb[k])) return false
  return true
}

export function stringListEqual(a: readonly string[], b: readonly string[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false
  return true
}

/** Candles only "change" when the bar count or the newest/oldest bar moves. */
export function candlesEqual(a: Candle[], b: Candle[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  if (a.length === 0) return true
  const ai = a[a.length - 1]
  const bi = b[b.length - 1]
  if (a[0][0] !== b[0][0]) return false
  for (let i = 0; i < 6; i += 1) if (ai[i] !== bi[i]) return false
  return true
}

/* --------------------------------------------------------------- hooks */

export function useStoreValue<T>(
  select: (s: SnapshotMessage | null) => T,
  isEqual: (a: T, b: T) => boolean = Object.is,
): T {
  const selectRef = useRef(select)
  selectRef.current = select
  const equalRef = useRef(isEqual)
  equalRef.current = isEqual
  const cache = useRef<{ has: boolean; value: T }>({ has: false, value: undefined as unknown as T })

  const get = useCallback(() => {
    const next = selectRef.current(snap)
    const c = cache.current
    if (!c.has || !equalRef.current(c.value, next)) {
      c.has = true
      c.value = next
    }
    return c.value
  }, [])

  return useSyncExternalStore(subscribe, get, get)
}

export function useConnected(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => connected,
    () => false,
  )
}

export function useHasSnapshot(): boolean {
  return useStoreValue((s) => s != null)
}

export function useFrameCount(): number {
  return useSyncExternalStore(
    subscribe,
    () => frames,
    () => 0,
  )
}

export interface FocusMeta {
  symbol: string
  asset: string
  last: number | null
  pct: number | null
  hi: number | null
  lo: number | null
  interval: string
  timeframe: string
}

const EMPTY_FOCUS: FocusMeta = {
  symbol: '',
  asset: 'SIM',
  last: null,
  pct: null,
  hi: null,
  lo: null,
  interval: '',
  timeframe: '',
}

export function useFocusMeta(): FocusMeta {
  return useStoreValue(
    (s) =>
      s
        ? {
            symbol: s.focus.symbol,
            asset: s.focus.asset,
            last: s.focus.last,
            pct: s.focus.pct,
            hi: s.focus.hi,
            lo: s.focus.lo,
            interval: s.focus.interval,
            timeframe: s.focus.timeframe,
          }
        : EMPTY_FOCUS,
    shallowEqual,
  )
}

export function useFocusSymbol(): string {
  return useStoreValue((s) => s?.focus.symbol ?? '')
}

/**
 * Resolved candle width only. Anything that just needs to *label* the chart uses
 * this instead of `useFocusMeta`, so a price tick does not re-render a form.
 */
export function useFocusInterval(): string {
  return useStoreValue((s) => s?.focus.interval ?? '')
}

const NO_CANDLES: Candle[] = []
export function useCandles(): Candle[] {
  return useStoreValue((s) => s?.focus.candles ?? NO_CANDLES, candlesEqual)
}

export function useDepth(): DepthLevels | null {
  return useStoreValue((s) => s?.focus.depth ?? null)
}

export function useFundamentals(): Fundamentals | null {
  return useStoreValue((s) => s?.focus.fundamentals ?? null, shallowEqual)
}

const DEFAULT_SETTINGS: SettingsView = {
  timeframe: '',
  chart_interval: '',
  chart_type: 'candles',
  show_volume: true,
  show_depth: true,
  equity_source: '',
  enable_equities: true,
  enable_crypto: true,
  theme: 'terminal',
}

export function useSettingsView(): SettingsView {
  return useStoreValue((s) => s?.settings ?? DEFAULT_SETTINGS, shallowEqual)
}

const DEFAULT_FEEDS: FeedStatus = { equities: 'off', crypto: 'off', detail: '' }
export function useFeeds(): FeedStatus {
  return useStoreValue((s) => s?.feeds ?? DEFAULT_FEEDS, shallowEqual)
}

export interface ChromeState {
  marketStatus: string
  source: string
  rawHz: number
  accel: string
  buyPct: number
  sellPct: number
}

const DEFAULT_CHROME: ChromeState = {
  marketStatus: '',
  source: '',
  rawHz: 0,
  accel: '',
  buyPct: 0,
  sellPct: 0,
}

export function useChrome(): ChromeState {
  return useStoreValue(
    (s) =>
      s
        ? {
            marketStatus: s.market_status,
            source: s.source,
            rawHz: s.raw_hz,
            accel: s.accel,
            buyPct: s.buy_pct,
            sellPct: s.sell_pct,
          }
        : DEFAULT_CHROME,
    shallowEqual,
  )
}

const NO_LEADERS: Leader[] = []
export function useHighs(): Leader[] {
  return useStoreValue((s) => s?.new_highs ?? NO_LEADERS)
}
export function useLows(): Leader[] {
  return useStoreValue((s) => s?.new_lows ?? NO_LEADERS)
}

const NO_TICKER: TickerEntry[] = []
export function useTicker(): TickerEntry[] {
  return useStoreValue((s) => s?.ticker ?? NO_TICKER)
}

const NO_WATCH: WatchRow[] = []
export function useWatchlist(): WatchRow[] {
  return useStoreValue((s) => s?.watchlist ?? NO_WATCH)
}

export function useBot(): BotView | null {
  return useStoreValue((s) => s?.bot ?? null)
}

/** Scalars only — used by the collapsed bot strip so it does not chase arrays. */
export interface BotSummary {
  present: boolean
  running: boolean
  paused: boolean
  halted: boolean
  warm: boolean
  mode: string
  equity: number
  dailyPnl: number
  openCount: number
  ticks: number
}

const NO_BOT: BotSummary = {
  present: false,
  running: false,
  paused: false,
  halted: false,
  warm: false,
  mode: '',
  equity: 0,
  dailyPnl: 0,
  openCount: 0,
  ticks: 0,
}

export function useBotSummary(): BotSummary {
  return useStoreValue(
    (s) =>
      s?.bot
        ? {
            present: true,
            running: s.bot.running,
            paused: s.bot.paused,
            halted: s.bot.halted,
            warm: s.bot.warm,
            mode: s.bot.mode,
            equity: s.bot.equity,
            dailyPnl: s.bot.daily_pnl,
            openCount: s.bot.open_count,
            ticks: s.bot.ticks,
          }
        : NO_BOT,
    shallowEqual,
  )
}

/* ------------------------------------------------------------- config */

/**
 * Persisted configuration (`/api/meta` + `/api/settings`). Fetched on mount and
 * after every accepted write; changes rarely, so it lives in its own store and
 * never rides the 10 Hz snapshot channel.
 */
export interface ConfigState {
  meta: MetaResponse | null
  settings: SettingsPayload | null
  error: string | null
  loading: boolean
}

let config: ConfigState = { meta: null, settings: null, error: null, loading: false }
const configSubs = new Set<() => void>()

export function setConfig(patch: Partial<ConfigState>) {
  config = { ...config, ...patch }
  for (const f of configSubs) f()
}

export function getConfig(): ConfigState {
  return config
}

export function resetConfig() {
  config = { meta: null, settings: null, error: null, loading: false }
  for (const f of configSubs) f()
}

function subscribeConfig(cb: () => void) {
  configSubs.add(cb)
  return () => {
    configSubs.delete(cb)
  }
}

export function useConfig(): ConfigState {
  return useSyncExternalStore(
    subscribeConfig,
    () => config,
    () => config,
  )
}
