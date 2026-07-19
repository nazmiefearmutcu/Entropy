import type { DepthLevels, Level } from '../contract'

const compact = (v: number) =>
  v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(2) + 'K' : v.toFixed(0)

function Row({ price, size, max, kind }: { price: number; size: number; max: number; kind: 'ask' | 'bid' }) {
  const w = max > 0 ? Math.max(size > 0 ? 6 : 0, Math.round(100 * (size / max))) : 0
  const color = kind === 'ask' ? 'text-red-500' : 'text-green-500'
  const bar = kind === 'ask' ? 'bg-red-900/50' : 'bg-green-900/50'
  return (
    <div className={`flex items-center gap-1 font-mono text-[11px] ${color}`}>
      <span className="w-16 text-right">{price.toFixed(2)}</span>
      <span className="relative h-3 flex-1">
        <span className={`absolute inset-y-0 left-0 ${bar}`} style={{ width: `${w}%` }} />
      </span>
      <span className="w-12 text-right">{compact(size)}</span>
    </div>
  )
}

export function DepthLadder({ symbol, view }: { symbol: string; view: DepthLevels | null }) {
  if (!view || (view.bids.length === 0 && view.asks.length === 0)) {
    return (
      <div className="p-2 border border-neutral-800 rounded">
        <div className="text-[11px] text-amber-400">DEPTH {symbol || '—'}</div>
        <div className="text-neutral-600 text-xs">—</div>
      </div>
    )
  }
  const N = 6
  const asks = [...view.asks].sort((a, b) => a[0] - b[0]).slice(0, N)
  const bids = [...view.bids].sort((a, b) => b[0] - a[0]).slice(0, N)
  const max = Math.max(0, ...asks.map((l) => l[1]), ...bids.map((l) => l[1]))
  const mode = view.is_synthetic ? 'SYNTH' : 'L1'
  const midNote =
    !view.is_synthetic && asks.length && bids.length
      ? `spread ${(asks[0][0] - bids[0][0]).toFixed(2)}`
      : 'rel.liq'
  return (
    <div className="p-2 border border-neutral-800 rounded">
      <div className="text-[11px] text-amber-400 uppercase">
        Depth {symbol} · {mode}·{view.basis}
      </div>
      {[...asks].reverse().map((l: Level, i) => (
        <Row key={'a' + i} price={l[0]} size={l[1]} max={max} kind="ask" />
      ))}
      <div className="text-center text-amber-400 text-[11px] my-0.5">
        ── {view.reference_price.toFixed(2)} {midNote} ──
      </div>
      {bids.map((l: Level, i) => (
        <Row key={'b' + i} price={l[0]} size={l[1]} max={max} kind="bid" />
      ))}
    </div>
  )
}
