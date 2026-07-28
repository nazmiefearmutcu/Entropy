import type { TickerEntry } from '../contract'
import { useTicker } from '../store'
import { Panel, PanelHead } from '../ui/layout'

/** Most-active tape per rolling window. Clicking a ticker focuses the chart. */
export function Ticker({
  ticker,
  onFocus,
}: {
  ticker: TickerEntry[]
  onFocus?: (symbol: string) => void
}) {
  return (
    <Panel className="shrink-0">
      <PanelHead title="Activity" />
      <div className="px-2.5 py-1.5">
        {ticker.length === 0 && <p className="py-1 text-xs text-ink-faint">no tape yet</p>}
        {ticker.map(([win, entries]) => (
          <div key={win} className="flex items-baseline gap-2 py-[3px]">
            <span className="w-8 shrink-0 font-mono text-micro uppercase text-ink-faint">{win}</span>
            <div className="flex min-w-0 gap-x-2.5 overflow-hidden">
              {entries.slice(0, 3).map(([sym, count]) => (
                <button
                  key={sym}
                  type="button"
                  onClick={() => onFocus?.(sym)}
                  title={`Focus ${sym}`}
                  className="shrink-0 whitespace-nowrap font-mono text-xs text-ink-dim hover:text-accent"
                >
                  {sym}
                  <span className="ml-1 tnum text-ink-faint">{count}</span>
                </button>
              ))}
              {entries.length === 0 && <span className="text-xs text-ink-faint">–</span>}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  )
}

export function TickerPanel({ onFocus }: { onFocus: (symbol: string) => void }) {
  const ticker = useTicker()
  return <Ticker ticker={ticker} onFocus={onFocus} />
}
