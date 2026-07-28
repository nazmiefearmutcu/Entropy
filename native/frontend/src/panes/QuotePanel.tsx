import type { Fundamentals } from '../contract'
import { compact, price as fmtPrice, DASH } from '../format'
import { useFocusMeta, useFundamentals } from '../store'
import { Chip } from '../ui/controls'
import { Delta, Stat } from '../ui/data'

/**
 * Quote readout strip under the chart. Hairline-separated cells rather than a
 * bordered card — it is supporting chrome for the hero, not a peer of it.
 */
export function QuotePanel(p: {
  symbol: string
  asset: string
  last: number | null
  pct: number | null
  hi: number | null
  lo: number | null
  fundamentals: Fundamentals | null
}) {
  const f = p.fundamentals
  const equity = p.asset === 'EQUITY'
  return (
    <div className="flex shrink-0 items-stretch gap-px bg-base">
      <div className="flex min-w-[128px] items-center gap-2 bg-panel px-2.5 py-1.5">
        <span className="font-mono text-sm font-semibold text-ink">{p.symbol || DASH}</span>
        <Chip tone={equity ? 'info' : 'accent'}>{p.asset}</Chip>
      </div>
      <div className="flex flex-1 items-center gap-5 overflow-x-auto bg-panel px-3 py-1.5">
        <Stat label="Last" value={fmtPrice(p.last)} />
        <div className="min-w-0">
          <div className="text-micro uppercase tracking-label text-ink-mute">Change</div>
          <Delta value={p.pct} />
        </div>
        <Stat label="Session hi" value={fmtPrice(p.hi)} tone="text-ink-dim" />
        <Stat label="Session lo" value={fmtPrice(p.lo)} tone="text-ink-dim" />
        {equity && (
          <>
            <span className="h-7 w-px shrink-0 bg-line" />
            <Stat
              label="P/E"
              value={f?.pe != null ? f.pe.toFixed(1) : DASH}
              tone="text-ink-dim"
              title="Trailing price / earnings"
            />
            <Stat
              label="Mkt cap"
              value={compact(f?.market_cap ?? null)}
              tone="text-ink-dim"
              title="Market capitalisation"
            />
            <Stat label="52w hi" value={fmtPrice(f?.high_52w ?? null)} tone="text-ink-dim" />
            <Stat label="52w lo" value={fmtPrice(f?.low_52w ?? null)} tone="text-ink-dim" />
          </>
        )}
      </div>
    </div>
  )
}

export function QuoteStrip() {
  const focus = useFocusMeta()
  const fundamentals = useFundamentals()
  return (
    <QuotePanel
      symbol={focus.symbol}
      asset={focus.asset}
      last={focus.last}
      pct={focus.pct}
      hi={focus.hi}
      lo={focus.lo}
      fundamentals={fundamentals}
    />
  )
}
