import type { DepthLevels, Level } from '../contract'
import { compact, price as fmtPrice } from '../format'
import { useDepth, useFocusSymbol, useSettingsView } from '../store'
import { openSettings } from '../ui-state'
import { Panel } from '../ui/layout'

const ROWS = 6

function Row({
  price,
  size,
  max,
  kind,
}: {
  price: number
  size: number
  max: number
  kind: 'ask' | 'bid'
}) {
  const w = max > 0 ? Math.max(size > 0 ? 4 : 0, Math.round(100 * (size / max))) : 0
  const ask = kind === 'ask'
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-[1px]">
      <span
        className={`w-[58px] shrink-0 text-right font-mono tnum text-xs ${
          ask ? 'text-down' : 'text-up'
        }`}
      >
        {fmtPrice(price)}
      </span>
      <span className="relative h-[11px] flex-1 overflow-hidden rounded-[1px] bg-sunken">
        <span
          className={`absolute inset-y-0 ${ask ? 'right-0 bg-down-soft' : 'left-0 bg-up-soft'}`}
          style={{ width: `${w}%` }}
        />
      </span>
      <span className="w-[46px] shrink-0 text-right font-mono tnum text-micro text-ink-mute">
        {compact(size)}
      </span>
    </div>
  )
}

/**
 * Order-book ladder. Asks descend into the reference price, bids fall away from
 * it, and synthetic books say so — a modelled book must never be mistaken for a
 * real one.
 */
export function DepthLadder({ symbol, view }: { symbol: string; view: DepthLevels | null }) {
  const empty = !view || (view.bids.length === 0 && view.asks.length === 0)

  const head = (
    <header className="relative flex h-[26px] shrink-0 items-center gap-2 bg-raised pl-2.5 pr-2 before:absolute before:left-0 before:top-0 before:h-full before:w-[2px] before:bg-accent">
      <h2 className="font-mono text-micro font-semibold uppercase tracking-label text-ink-dim">
        DEPTH {symbol || '–'}
      </h2>
      {view && (
        <span
          title={
            view.is_synthetic
              ? `Synthetic book modelled from ${view.basis} — relative liquidity, not real resting orders`
              : `Top-of-book from ${view.basis}`
          }
          className={`ml-auto inline-flex h-[15px] items-center rounded-[2px] px-1 font-mono text-micro font-semibold ${
            view.is_synthetic ? 'bg-accent-soft text-accent' : 'bg-info-soft text-info'
          }`}
        >
          {view.is_synthetic ? 'SYNTH' : 'L1'}·{view.basis}
        </span>
      )}
    </header>
  )

  if (empty) {
    return (
      <Panel className="shrink-0">
        {head}
        <p className="px-2.5 py-2 text-xs leading-relaxed text-ink-mute">
          No book for this symbol. Depth needs a live equity source — in sim mode the sidecar sends
          nothing here.
          <button
            type="button"
            onClick={() => openSettings('feeds')}
            className="ml-1 text-ink-dim underline decoration-dotted hover:text-accent"
          >
            Check feeds
          </button>
        </p>
      </Panel>
    )
  }

  const asks = [...view.asks].sort((a, b) => a[0] - b[0]).slice(0, ROWS)
  const bids = [...view.bids].sort((a, b) => b[0] - a[0]).slice(0, ROWS)
  const max = Math.max(0, ...asks.map((l) => l[1]), ...bids.map((l) => l[1]))
  const midNote =
    !view.is_synthetic && asks.length && bids.length
      ? `spread ${(asks[0][0] - bids[0][0]).toFixed(2)}`
      : 'rel.liq'

  return (
    <Panel className="shrink-0">
      {head}
      <div className="py-1">
        {[...asks].reverse().map((l: Level, i) => (
          <Row key={`a${i}`} price={l[0]} size={l[1]} max={max} kind="ask" />
        ))}
        <div className="my-1 flex items-center gap-2 px-2.5">
          <span className="h-px flex-1 bg-line" />
          <span className="font-mono tnum text-sm font-semibold text-ink">
            {fmtPrice(view.reference_price)}
          </span>
          <span className="font-mono text-micro uppercase text-ink-mute">{midNote}</span>
          <span className="h-px flex-1 bg-line" />
        </div>
        {bids.map((l: Level, i) => (
          <Row key={`b${i}`} price={l[0]} size={l[1]} max={max} kind="bid" />
        ))}
      </div>
    </Panel>
  )
}

export function DepthPanel() {
  const symbol = useFocusSymbol()
  const view = useDepth()
  const settings = useSettingsView()
  if (!settings.show_depth) {
    return (
      <Panel className="shrink-0">
        <div className="flex items-center gap-2 px-2.5 py-1.5">
          <span className="font-mono text-micro uppercase tracking-label text-ink-faint">
            DEPTH off
          </span>
          <button
            type="button"
            onClick={() => openSettings('chart')}
            className="ml-auto text-micro text-ink-mute underline decoration-dotted hover:text-accent"
          >
            enable in settings
          </button>
        </div>
      </Panel>
    )
  }
  return <DepthLadder symbol={symbol} view={view} />
}
