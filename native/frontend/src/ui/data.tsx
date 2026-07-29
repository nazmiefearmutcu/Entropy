import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { dirClass, dirGlyph, pct as fmtPct, signed } from '../format'

/**
 * A signed delta. Colour is never the only signal: the sign and the direction
 * glyph carry the same information for colour-vision-deficient readers.
 */
export function Delta({
  value,
  kind = 'pct',
  digits = 2,
  size = 'sm',
}: {
  value: number | null
  kind?: 'pct' | 'abs'
  digits?: number
  size?: 'micro' | 'sm' | 'md' | 'lg'
}) {
  const cls = dirClass(value)
  const text = kind === 'pct' ? fmtPct(value, digits) : signed(value, digits)
  const sz = { micro: 'text-micro', sm: 'text-sm', md: 'text-md', lg: 'text-lg' }[size]
  return (
    <span className={`inline-flex items-center gap-1 font-mono tnum ${sz} ${cls}`}>
      <span aria-hidden="true" className="text-[0.72em] leading-none">
        {dirGlyph(value)}
      </span>
      {text}
    </span>
  )
}

/** Briefly tints its child when the tracked number moves. */
export function Flash({
  value,
  children,
  className = '',
}: {
  value: number | null
  children: ReactNode
  className?: string
}) {
  const prev = useRef<number | null>(value)
  const [flash, setFlash] = useState('')
  useEffect(() => {
    const before = prev.current
    prev.current = value
    if (before == null || value == null || before === value) return
    setFlash(value > before ? 'flash-up' : 'flash-down')
    const t = setTimeout(() => setFlash(''), 480)
    return () => clearTimeout(t)
  }, [value])
  return <span className={`rounded-[2px] ${flash} ${className}`}>{children}</span>
}

/** Micro label over a figure. The workhorse of every readout strip. */
export function Stat({
  label,
  value,
  tone,
  title,
  size = 'sm',
}: {
  label: string
  value: ReactNode
  tone?: string
  title?: string
  size?: 'sm' | 'md' | 'lg'
}) {
  const sz = { sm: 'text-sm', md: 'text-md', lg: 'text-lg' }[size]
  return (
    <div className="min-w-0" title={title}>
      <div className="truncate text-micro uppercase tracking-label text-ink-mute">{label}</div>
      <div className={`truncate font-mono tnum ${sz} ${tone ?? 'text-ink'}`}>{value}</div>
    </div>
  )
}

/**
 * Sparkline. One series, so no legend — the row's ticker names it. Direction is
 * encoded by slope and reinforced by the adjacent signed percentage.
 */
export function Sparkline({
  values,
  width = 56,
  height = 16,
  positive,
}: {
  values: number[]
  width?: number
  height?: number
  positive: boolean
}) {
  if (values.length < 2) {
    return (
      <svg width={width} height={height} aria-hidden="true">
        <line
          x1="0"
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="var(--ink-faint)"
          strokeWidth="1"
          strokeDasharray="2 3"
        />
      </svg>
    )
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 1.5
  const h = height - pad * 2
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width
      const y = pad + h - ((v - min) / span) * h
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  const stroke = positive ? 'var(--up)' : 'var(--down)'
  return (
    <svg width={width} height={height} aria-hidden="true" className="block">
      <polyline
        points={pts}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}

/** Horizontal proportion bar with a 2px surface gap between the two fills. */
export function SplitBar({
  left,
  right,
  height = 8,
}: {
  left: number
  right: number
  height?: number
}) {
  const total = Math.max(1, left + right)
  return (
    <div className="flex w-full overflow-hidden rounded-[2px] bg-sunken" style={{ height }}>
      <div className="bg-up" style={{ width: `${(left / total) * 100}%` }} />
      <div className="w-[2px] shrink-0 bg-panel" />
      <div className="bg-down" style={{ width: `${(right / total) * 100}%` }} />
    </div>
  )
}
