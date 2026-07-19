function spark(vals: number[]) {
  if (vals.length < 2) return null
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const span = max - min || 1
  const pts = vals
    .map((v, i) => `${(i / (vals.length - 1)) * 40},${12 - ((v - min) / span) * 12}`)
    .join(' ')
  return (
    <svg width="40" height="12" className="inline-block align-middle">
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1" />
    </svg>
  )
}

export function Watchlist({ rows }: { rows: [string, number | null, number | null, number[]][] }) {
  return (
    <div className="p-2 border border-neutral-800 rounded">
      <div className="text-[11px] uppercase text-neutral-500">Watchlist</div>
      {rows.length === 0 && <div className="text-neutral-600 text-xs">—</div>}
      {rows.map(([sym, last, pct, sp]) => (
        <div key={sym} className="flex items-center gap-2 text-xs py-0.5">
          <span className="flex-1">{sym}</span>
          <span>{last != null ? last.toFixed(2) : '—'}</span>
          <span className={(pct ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'}>
            {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%` : '—'}
          </span>
          <span className="text-sky-400">{spark(sp)}</span>
        </div>
      ))}
    </div>
  )
}
