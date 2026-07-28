import { useState } from 'react'
import type { WatchRow } from '../contract'
import { removeWatch } from '../api'
import { pct as fmtPct, price as fmtPrice, dirGlyph } from '../format'
import { usePort } from '../port'
import { useFocusSymbol, useWatchlist } from '../store'
import { reportAck } from '../toast'
import { openPicker } from '../ui-state'
import { Button, IconButton } from '../ui/controls'
import { Sparkline } from '../ui/data'
import { EmptyState, Panel, PanelHead } from '../ui/layout'

/**
 * The followed-symbols list. Every row is actionable: click focuses the chart,
 * the star removes it. Adding goes through the same symbol picker as Cmd+K.
 */
export function Watchlist({
  rows,
  activeSymbol,
  onFocus,
  onRemove,
  onAdd,
  busy,
}: {
  rows: WatchRow[]
  activeSymbol?: string
  onFocus: (symbol: string) => void
  onRemove: (symbol: string) => void
  onAdd: () => void
  busy?: string | null
}) {
  return (
    <Panel className="min-h-0 flex-1">
      <PanelHead
        title="Watchlist"
        accent="accent"
        meta={`${rows.length} followed`}
        actions={<IconButton icon="plus" label="Add symbol to watchlist" onClick={onAdd} size={20} />}
      />
      {rows.length === 0 ? (
        <EmptyState
          title="No symbols followed yet"
          hint="Follow a symbol to keep its last price and intraday shape in view. The same list feeds the bot's default universe."
          action={
            <Button variant="primary" size="sm" onClick={onAdd}>
              Add symbol
            </Button>
          }
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {rows.map(([sym, ticker, name, exchange, last, pct, spark]) => {
            const active = sym === activeSymbol
            const up = (pct ?? 0) >= 0
            return (
              <div
                key={sym}
                className={`group flex items-center gap-2 border-l-2 pl-2 pr-1 transition-colors duration-100 ${
                  active
                    ? 'border-accent bg-accent-soft'
                    : 'border-transparent hover:border-line-strong hover:bg-hover'
                }`}
              >
                <button
                  type="button"
                  onClick={() => onFocus(sym)}
                  title={`${name || ticker}${exchange ? ` — ${exchange}` : ''}`}
                  className="flex min-w-0 flex-1 items-center gap-2 py-1 text-left"
                >
                  {/* The exchange's ticker, not the canonical id: "BTCUSDT",
                      not "binance-s…". The venue rides underneath as a badge so
                      the two are never confused for one another. */}
                  <span className="flex w-[86px] shrink-0 flex-col leading-tight">
                    <span
                      className={`truncate font-mono text-xs font-medium ${
                        active ? 'text-accent' : 'text-ink'
                      }`}
                    >
                      {ticker || sym}
                    </span>
                    {exchange && (
                      <span className="truncate text-[9px] uppercase tracking-label text-ink-faint">
                        {exchange}
                      </span>
                    )}
                  </span>
                  <Sparkline values={spark} positive={up} width={52} height={16} />
                  <span className="ml-auto shrink-0 text-right font-mono tnum text-xs text-ink-dim">
                    {fmtPrice(last)}
                  </span>
                  <span
                    className={`w-[56px] shrink-0 text-right font-mono tnum text-xs ${
                      pct == null ? 'text-ink-faint' : up ? 'text-up' : 'text-down'
                    }`}
                  >
                    {pct != null && (
                      <span aria-hidden="true" className="mr-0.5 text-[0.7em]">
                        {dirGlyph(pct)}
                      </span>
                    )}
                    {fmtPct(pct, 1)}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`Remove ${ticker || sym} from watchlist`}
                  title={`Remove ${ticker || sym}`}
                  disabled={busy === sym}
                  onClick={() => onRemove(sym)}
                  className="shrink-0 rounded-[2px] px-1 py-1 text-ink-faint opacity-0 transition-opacity duration-100 hover:text-down focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-40"
                >
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                    <path d="M3.4 2.3 8 6.9l4.6-4.6 1.1 1.1L9.1 8l4.6 4.6-1.1 1.1L8 9.1l-4.6 4.6-1.1-1.1L6.9 8 2.3 3.4Z" />
                  </svg>
                </button>
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}

export function WatchlistPanel({ onFocus }: { onFocus: (symbol: string) => void }) {
  const rows = useWatchlist()
  const active = useFocusSymbol()
  const port = usePort()
  const [busy, setBusy] = useState<string | null>(null)

  const remove = async (symbol: string) => {
    setBusy(symbol)
    const ack = await removeWatch(port, symbol)
    setBusy(null)
    reportAck(ack)
  }

  return (
    <Watchlist
      rows={rows}
      activeSymbol={active}
      onFocus={onFocus}
      onRemove={(s) => void remove(s)}
      onAdd={() => openPicker('watch')}
      busy={busy}
    />
  )
}
