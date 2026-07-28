import { useCallback, useEffect, useRef } from 'react'
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { Candle } from '../contract'

const UP = '#16c784'
const DOWN = '#ea3943'
const EMA_FAST = '#ffb020'
const EMA_SLOW = '#4ea8ff'
const SURFACE = '#0b0c0e'
const GRID = '#191d21'
const AXIS_INK = '#6b767d'

function ema(vals: number[], period: number): number[] {
  const k = 2 / (Math.max(1, period) + 1)
  const out: number[] = []
  let prev = vals[0] ?? 0
  vals.forEach((v, i) => {
    prev = i === 0 ? v : v * k + prev * (1 - k)
    out.push(prev)
  })
  return out
}

/**
 * The hero. Candles or line, an optional volume pane, and two EMA overlays whose
 * periods come from the bot's own indicator config so the picture on screen and
 * the signal the bot computes cannot drift apart.
 */
export function FocusChart({
  candles,
  symbol,
  chartType,
  showVolume,
  emaFast,
  emaSlow,
  intervalSeconds,
}: {
  candles: Candle[]
  symbol: string
  chartType: string
  showVolume: boolean
  emaFast: number
  emaSlow: number
  intervalSeconds: number
}) {
  const host = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const priceRef = useRef<ISeriesApi<'Candlestick'> | ISeriesApi<'Line'> | null>(null)
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const fastRef = useRef<ISeriesApi<'Line'> | null>(null)
  const slowRef = useRef<ISeriesApi<'Line'> | null>(null)
  const dataRef = useRef<Candle[]>(candles)
  dataRef.current = candles
  const painted = useRef<{ len: number; firstTs: number } | null>(null)
  const line = chartType === 'line'

  const paint = useCallback(() => {
    const rows = dataRef.current
    if (!priceRef.current || rows.length === 0) return
    const times = rows.map(([ts]) => Math.floor(ts / 1e9) as UTCTimestamp)
    const closes = rows.map((r) => r[4])
    const f = ema(closes, emaFast)
    const s = ema(closes, emaSlow)
    const last = rows.length - 1

    const prev = painted.current
    // The common case at 10 Hz is "only the newest bar moved". Replacing the whole
    // series then would throw the user's pan/zoom away every frame, so patch the
    // tail instead. EMA is causal, so only its last point changes too.
    const tailOnly = prev != null && prev.len === rows.length && prev.firstTs === times[0]

    const bar = (i: number) => {
      const [, o, h, l, c] = rows[i]
      return { time: times[i], open: o, high: h, low: l, close: c }
    }
    const vol = (i: number) => ({
      time: times[i],
      value: rows[i][5],
      color: rows[i][4] >= rows[i][1] ? 'rgba(22,199,132,0.42)' : 'rgba(234,57,67,0.42)',
    })

    if (tailOnly) {
      if (line) {
        ;(priceRef.current as ISeriesApi<'Line'>).update({ time: times[last], value: closes[last] })
      } else {
        ;(priceRef.current as ISeriesApi<'Candlestick'>).update(bar(last))
      }
      volRef.current?.update(vol(last))
      fastRef.current?.update({ time: times[last], value: f[last] })
      slowRef.current?.update({ time: times[last], value: s[last] })
    } else {
      if (line) {
        ;(priceRef.current as ISeriesApi<'Line'>).setData(
          times.map((t, i) => ({ time: t, value: closes[i] })),
        )
      } else {
        ;(priceRef.current as ISeriesApi<'Candlestick'>).setData(rows.map((_, i) => bar(i)))
      }
      volRef.current?.setData(rows.map((_, i) => vol(i)))
      fastRef.current?.setData(times.map((t, i) => ({ time: t, value: f[i] })))
      slowRef.current?.setData(times.map((t, i) => ({ time: t, value: s[i] })))
    }

    painted.current = { len: rows.length, firstTs: times[0] }
  }, [line, emaFast, emaSlow])

  useEffect(() => {
    if (!host.current) return
    const chart = createChart(host.current, {
      layout: {
        background: { type: ColorType.Solid, color: SURFACE },
        textColor: AXIS_INK,
        fontFamily: 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace',
        fontSize: 10,
      },
      grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      rightPriceScale: { borderColor: '#22262a' },
      timeScale: {
        borderColor: '#22262a',
        timeVisible: true,
        secondsVisible: intervalSeconds > 0 && intervalSeconds < 60,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#4b545b', width: 1, style: 3, labelBackgroundColor: '#262b30' },
        horzLine: { color: '#4b545b', width: 1, style: 3, labelBackgroundColor: '#262b30' },
      },
      watermark: {
        visible: Boolean(symbol),
        text: symbol,
        color: 'rgba(233,237,240,0.045)',
        fontSize: 54,
        fontFamily: 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace',
        horzAlign: 'center',
        vertAlign: 'center',
      },
      autoSize: true,
    })
    chartRef.current = chart

    priceRef.current = line
      ? chart.addLineSeries({ color: UP, lineWidth: 2, priceLineVisible: false })
      : chart.addCandlestickSeries({
          upColor: UP,
          downColor: DOWN,
          wickUpColor: UP,
          wickDownColor: DOWN,
          borderVisible: false,
        })

    if (showVolume) {
      const vol = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'entropy-volume',
        lastValueVisible: false,
        priceLineVisible: false,
      })
      vol.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } })
      volRef.current = vol
      priceRef.current.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.2 } })
    } else {
      volRef.current = null
      priceRef.current.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.06 } })
    }

    fastRef.current = chart.addLineSeries({
      color: EMA_FAST,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })
    slowRef.current = chart.addLineSeries({
      color: EMA_SLOW,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })

    painted.current = null
    paint()

    return () => {
      chart.remove()
      chartRef.current = null
      priceRef.current = null
      volRef.current = null
      fastRef.current = null
      slowRef.current = null
    }
  }, [line, showVolume, symbol, intervalSeconds, paint])

  useEffect(() => {
    paint()
  }, [candles, paint])

  return (
    <div className="relative min-h-0 flex-1 bg-sunken">
      <div ref={host} className="absolute inset-0" />
      <div className="pointer-events-none absolute left-2.5 top-2 flex items-center gap-3 font-mono text-micro">
        <span className="flex items-center gap-1 text-ink-mute">
          <span className="inline-block h-[2px] w-3 rounded-full" style={{ background: EMA_FAST }} />
          EMA {emaFast}
        </span>
        <span className="flex items-center gap-1 text-ink-mute">
          <span className="inline-block h-[2px] w-3 rounded-full" style={{ background: EMA_SLOW }} />
          EMA {emaSlow}
        </span>
        {showVolume && <span className="text-ink-faint">VOL</span>}
      </div>
      {candles.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="text-xs text-ink-mute">waiting for bars…</p>
        </div>
      )}
    </div>
  )
}
