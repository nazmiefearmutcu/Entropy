export function Header(props: {
  marketStatus: string
  source: string
  clock: string
  indices: [string, number, number][]
}) {
  return (
    <div className="flex items-center gap-3 px-3 py-1 border-b border-neutral-800 text-sm">
      <span className="font-semibold tracking-wide">Entropy</span>
      <span className="text-neutral-400">{props.clock}</span>
      <span className={props.marketStatus === 'open' ? 'text-green-500' : 'text-red-500'}>
        NYSE {props.marketStatus || '—'}
      </span>
      {props.indices.map(([sym, px, pct]) => (
        <span key={sym} className="text-neutral-400">
          {sym} {px.toFixed(2)}{' '}
          <span className={pct >= 0 ? 'text-green-500' : 'text-red-500'}>
            {pct >= 0 ? '+' : ''}
            {pct.toFixed(2)}%
          </span>
        </span>
      ))}
      <span className="ml-auto text-sky-400">source: {props.source}</span>
    </div>
  )
}
