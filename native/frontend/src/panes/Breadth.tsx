export function Breadth(p: { buyPct: number; sellPct: number; rawHz: number; accel: string }) {
  return (
    <div className="p-2 border border-neutral-800 rounded">
      <div className="text-[11px] uppercase text-neutral-500">Breadth</div>
      <div className="flex h-2.5 rounded overflow-hidden my-1.5">
        <div className="bg-green-600" style={{ width: `${p.buyPct}%` }} />
        <div className="bg-red-600" style={{ width: `${p.sellPct}%` }} />
      </div>
      <div className="text-xs text-neutral-400">
        B {p.buyPct.toFixed(0)}% · S {p.sellPct.toFixed(0)}%
        <br />
        {p.rawHz.toFixed(0)} Hz · {p.accel}
      </div>
    </div>
  )
}
