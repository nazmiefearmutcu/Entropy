import type { TickerEntry } from '../contract'

export function Ticker(p: { ticker: TickerEntry[] }) {
  return (
    <div className="p-2 border border-neutral-800 rounded text-xs text-neutral-400">
      <div className="text-[11px] uppercase text-neutral-500">Activity</div>
      {p.ticker.map(([win, entries]) => (
        <div key={win}>
          {win}: {entries.slice(0, 3).map(([s, c]) => `${s} ${c}`).join(' · ')}
        </div>
      ))}
    </div>
  )
}
