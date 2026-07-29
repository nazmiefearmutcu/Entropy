import { useChrome } from '../store'
import { SplitBar } from '../ui/data'

/**
 * Session breadth. Two figures and one bar — the buy/sell split is the headline
 * of the scanner rail, so it gets size instead of another bordered box.
 */
export function Breadth(p: { buyPct: number; sellPct: number; rawHz: number; accel: string }) {
  const lead = p.buyPct >= p.sellPct ? 'buy' : 'sell'
  return (
    <div className="shrink-0 bg-panel px-2.5 py-2">
      <div className="flex items-baseline justify-between">
        <span className="text-micro uppercase tracking-label text-ink-mute">Breadth</span>
        <span className="font-mono text-micro uppercase text-ink-faint">{p.accel || 'steady'}</span>
      </div>

      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span
          className={`font-mono tnum text-lg font-semibold ${
            lead === 'buy' ? 'text-up' : 'text-ink-dim'
          }`}
        >
          {p.buyPct.toFixed(0)}
          <span className="text-xs">%</span>
        </span>
        <span className="text-micro uppercase tracking-label text-ink-faint">buy</span>
        <span className="ml-auto text-micro uppercase tracking-label text-ink-faint">sell</span>
        <span
          className={`font-mono tnum text-lg font-semibold ${
            lead === 'sell' ? 'text-down' : 'text-ink-dim'
          }`}
        >
          {p.sellPct.toFixed(0)}
          <span className="text-xs">%</span>
        </span>
      </div>

      <div className="mt-1.5">
        <SplitBar left={p.buyPct} right={p.sellPct} />
      </div>

      <div className="mt-1.5 flex items-center justify-between text-micro text-ink-mute">
        <span>
          tape <span className="font-mono tnum text-ink-dim">{p.rawHz.toFixed(0)} Hz</span>
        </span>
        <span className="uppercase tracking-label">{lead} pressure</span>
      </div>
    </div>
  )
}

export function BreadthPanel() {
  const { buyPct, sellPct, rawHz, accel } = useChrome()
  return <Breadth buyPct={buyPct} sellPct={sellPct} rawHz={rawHz} accel={accel} />
}
