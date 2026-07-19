import type { Leader } from '../contract'

function Board({
  title,
  rows,
  tone,
  onFocus,
}: {
  title: string
  rows: Leader[]
  tone: 'up' | 'down'
  onFocus: (s: string) => void
}) {
  const head = tone === 'up' ? 'text-green-500' : 'text-red-500'
  return (
    <div className="flex flex-col border border-neutral-800 rounded min-h-0 overflow-hidden">
      <div className={`text-[11px] uppercase px-2 py-1 border-b border-neutral-800 ${head}`}>{title}</div>
      <div className="flex-1 overflow-y-auto">
        {rows.map(([sym, count, price, pct]) => (
          <div
            key={sym}
            className="flex justify-between px-2 py-0.5 text-xs cursor-pointer hover:bg-neutral-900"
            onClick={() => onFocus(sym)}
          >
            <span className="flex-1">{sym}</span>
            <span className="w-8 text-right text-neutral-500">{count}</span>
            <span className="w-16 text-right">{price.toFixed(2)}</span>
            <span className={`w-14 text-right ${pct >= 0 ? 'text-green-500' : 'text-red-500'}`}>
              {pct >= 0 ? '+' : ''}
              {pct.toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function ScannerBoards({
  highs,
  lows,
  onFocus,
}: {
  highs: Leader[]
  lows: Leader[]
  onFocus: (s: string) => void
}) {
  return (
    <div className="grid grid-rows-2 gap-2 min-h-0">
      <Board title="Session new highs" rows={highs} tone="up" onFocus={onFocus} />
      <Board title="On new lows" rows={lows} tone="down" onFocus={onFocus} />
    </div>
  )
}
