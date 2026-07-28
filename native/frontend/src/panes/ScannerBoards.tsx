import { memo } from 'react'
import type { Leader } from '../contract'
import { pct as fmtPct, price as fmtPrice, dirGlyph } from '../format'
import { useHighs, useLows, useFocusSymbol, useSettingsView } from '../store'
import { openSettings } from '../ui-state'

const MAX_ROWS = 120

const Row = memo(function Row({
  sym,
  count,
  price,
  pct,
  active,
  onFocus,
}: {
  sym: string
  count: number
  price: number
  pct: number
  active: boolean
  onFocus: (s: string) => void
}) {
  const up = pct >= 0
  return (
    <button
      type="button"
      onClick={() => onFocus(sym)}
      aria-current={active ? 'true' : undefined}
      className={`group flex w-full items-center gap-1.5 border-l-2 px-2 py-[3px] text-left transition-colors duration-100 ${
        active
          ? 'border-accent bg-accent-soft'
          : 'border-transparent hover:border-line-strong hover:bg-hover'
      }`}
    >
      <span
        className={`min-w-0 flex-1 truncate font-mono text-xs font-medium ${
          active ? 'text-accent' : 'text-ink'
        }`}
      >
        {sym}
      </span>
      <span
        className="w-7 shrink-0 text-right font-mono tnum text-micro text-ink-faint"
        title={`${count} breaks this session`}
      >
        {count}
      </span>
      <span className="w-[62px] shrink-0 text-right font-mono tnum text-xs text-ink-dim">
        {fmtPrice(price)}
      </span>
      <span
        className={`w-[58px] shrink-0 text-right font-mono tnum text-xs ${
          up ? 'text-up' : 'text-down'
        }`}
      >
        <span aria-hidden="true" className="mr-0.5 text-[0.7em]">
          {dirGlyph(pct)}
        </span>
        {fmtPct(pct, 1)}
      </span>
    </button>
  )
})

function Board({
  title,
  rows,
  tone,
  onFocus,
  activeSymbol,
}: {
  title: string
  rows: Leader[]
  tone: 'up' | 'down'
  onFocus: (s: string) => void
  activeSymbol?: string
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col bg-panel">
      <header
        className={`relative flex h-[26px] shrink-0 items-center gap-2 bg-raised pl-2.5 pr-2 before:absolute before:left-0 before:top-0 before:h-full before:w-[2px] ${
          tone === 'up' ? 'before:bg-up' : 'before:bg-down'
        }`}
      >
        <h2
          className={`text-micro font-semibold uppercase tracking-label ${
            tone === 'up' ? 'text-up' : 'text-down'
          }`}
        >
          {title}
        </h2>
        <span className="ml-auto font-mono tnum text-micro text-ink-faint">{rows.length}</span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.length === 0 && (
          <p className="px-2.5 py-2 text-xs text-ink-faint">nothing breaking this window</p>
        )}
        {rows.slice(0, MAX_ROWS).map(([sym, count, price, pct]) => (
          <Row
            key={sym}
            sym={sym}
            count={count}
            price={price}
            pct={pct}
            active={sym === activeSymbol}
            onFocus={onFocus}
          />
        ))}
      </div>
    </section>
  )
}

export function ScannerBoards({
  highs,
  lows,
  onFocus,
  activeSymbol,
  timeframe,
}: {
  highs: Leader[]
  lows: Leader[]
  onFocus: (s: string) => void
  activeSymbol?: string
  timeframe?: string
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-px bg-base">
      <div className="flex h-[24px] shrink-0 items-center gap-2 bg-panel px-2.5">
        <span className="text-micro font-semibold uppercase tracking-label text-ink-dim">
          Scanner
        </span>
        {timeframe != null && (
          <button
            type="button"
            onClick={() => openSettings('scanner')}
            title="Scanner timeframe — the rolling window for highs, lows and momentum. Click to change."
            className="ml-auto inline-flex h-[17px] items-center gap-1 rounded-[2px] bg-accent-soft px-1.5 font-mono text-micro font-semibold text-accent hover:brightness-125"
          >
            TF {timeframe || 'auto'}
          </button>
        )}
      </div>
      <Board
        title="New highs"
        rows={highs}
        tone="up"
        onFocus={onFocus}
        activeSymbol={activeSymbol}
      />
      <Board title="New lows" rows={lows} tone="down" onFocus={onFocus} activeSymbol={activeSymbol} />
    </div>
  )
}

export function ScannerPanel({ onFocus }: { onFocus: (s: string) => void }) {
  const highs = useHighs()
  const lows = useLows()
  const active = useFocusSymbol()
  const settings = useSettingsView()
  return (
    <ScannerBoards
      highs={highs}
      lows={lows}
      onFocus={onFocus}
      activeSymbol={active}
      timeframe={settings.timeframe}
    />
  )
}
