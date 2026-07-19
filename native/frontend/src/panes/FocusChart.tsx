import { useEffect, useRef } from 'react'
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { Candle } from '../contract'

function ema(vals: number[], period: number): number[] {
  const k = 2 / (period + 1)
  const out: number[] = []
  let prev = vals[0] ?? 0
  vals.forEach((v, i) => {
    prev = i === 0 ? v : v * k + prev * (1 - k)
    out.push(prev)
  })
  return out
}

export function FocusChart({ candles }: { candles: Candle[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const ema9Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ema21Ref = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: '#0a0a0a' }, textColor: '#c8c8c8' },
      grid: { vertLines: { color: '#1a1a1a' }, horzLines: { color: '#1a1a1a' } },
      timeScale: { timeVisible: true },
      autoSize: true,
    })
    chartRef.current = chart
    candleRef.current = chart.addCandlestickSeries({
      upColor: '#26d626',
      downColor: '#ff3b3b',
      wickUpColor: '#26d626',
      wickDownColor: '#ff3b3b',
      borderVisible: false,
    })
    ema9Ref.current = chart.addLineSeries({ color: '#e6c200', lineWidth: 1 })
    ema21Ref.current = chart.addLineSeries({ color: '#7a7aff', lineWidth: 1 })
    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!candleRef.current || candles.length === 0) return
    const bars = candles.map(([ts, o, h, l, c]) => ({
      time: Math.floor(ts / 1e9) as UTCTimestamp,
      open: o,
      high: h,
      low: l,
      close: c,
    }))
    candleRef.current.setData(bars)
    const closes = candles.map((c) => c[4])
    const times = bars.map((b) => b.time)
    const e9 = ema(closes, 9)
    const e21 = ema(closes, 21)
    ema9Ref.current?.setData(times.map((t, i) => ({ time: t, value: e9[i] })))
    ema21Ref.current?.setData(times.map((t, i) => ({ time: t, value: e21[i] })))
  }, [candles])

  return <div ref={ref} className="border border-neutral-800 rounded min-h-0 flex-1" />
}
