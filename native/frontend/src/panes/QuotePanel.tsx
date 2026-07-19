import type { Fundamentals } from '../contract'

const dash = '—'

const compact = (v: number) =>
  v >= 1e12
    ? (v / 1e12).toFixed(2) + 'T'
    : v >= 1e9
      ? (v / 1e9).toFixed(2) + 'B'
      : v >= 1e6
        ? (v / 1e6).toFixed(2) + 'M'
        : v.toFixed(2)

function fundLine(f: Fundamentals | null) {
  if (!f) return `P/E ${dash} · MktCap ${dash} · 52w ${dash}/${dash}`
  const pe = f.pe != null ? f.pe.toFixed(1) : dash
  const cap = f.market_cap != null ? compact(f.market_cap) : dash
  const hi = f.high_52w != null ? f.high_52w.toFixed(2) : dash
  const lo = f.low_52w != null ? f.low_52w.toFixed(2) : dash
  return `P/E ${pe} · MktCap ${cap} · 52w ${hi}/${lo}`
}

export function QuotePanel(p: {
  symbol: string
  asset: string
  last: number | null
  pct: number | null
  hi: number | null
  lo: number | null
  fundamentals: Fundamentals | null
}) {
  return (
    <div className="p-2 border border-neutral-800 rounded text-xs">
      <div>
        <span className="font-semibold">{p.symbol}</span>{' '}
        <span className="text-[10px] bg-sky-500/20 text-sky-400 px-1 rounded">{p.asset}</span>
      </div>
      <div>
        Last {p.last != null ? p.last.toFixed(2) : dash}{' '}
        {p.pct != null && (
          <span className={p.pct >= 0 ? 'text-green-500' : 'text-red-500'}>
            {p.pct >= 0 ? '+' : ''}
            {p.pct.toFixed(2)}%
          </span>
        )}
      </div>
      <div className="text-neutral-400">
        Hi {p.hi != null ? p.hi.toFixed(2) : dash} Lo {p.lo != null ? p.lo.toFixed(2) : dash}
      </div>
      {p.asset === 'EQUITY' && <div className="text-neutral-600">{fundLine(p.fundamentals)}</div>}
    </div>
  )
}
