import { useEffect, useState } from 'react'
import { StreamClient, postCommand } from './ws'
import { setSnap, setConnected, useSnap, useConnected } from './store'
import { Header } from './panes/Header'
import { Breadth } from './panes/Breadth'
import { Ticker } from './panes/Ticker'
import { ScannerBoards } from './panes/ScannerBoards'
import { Watchlist } from './panes/Watchlist'
import { FocusChart } from './panes/FocusChart'
import { QuotePanel } from './panes/QuotePanel'
import { DepthLadder } from './panes/DepthLadder'
import { CommandBar } from './panes/CommandBar'

declare global {
  interface Window {
    __SIDECAR_PORT__?: number
  }
}

function resolvePort(): number {
  const q = new URLSearchParams(location.search).get('port')
  if (q) return parseInt(q, 10)
  return window.__SIDECAR_PORT__ ?? 8000
}

export default function App() {
  const [port] = useState(resolvePort)
  useEffect(() => {
    const c = new StreamClient(port, { onSnapshot: setSnap, onStatus: setConnected })
    c.connect()
    return () => c.stop()
  }, [port])
  const s = useSnap()
  const connected = useConnected()
  const cmd = (verb: string, arg: string) => {
    void postCommand(port, verb, arg)
  }

  if (!s) return <div className="p-4 text-neutral-500">connecting to sidecar on :{port}…</div>
  return (
    <div className="h-screen flex flex-col">
      <Header
        marketStatus={s.market_status}
        source={s.source}
        clock={new Date().toLocaleTimeString()}
        indices={[]}
      />
      <div className="flex-1 grid grid-cols-[160px_1fr_280px] gap-2 p-2 min-h-0">
        <div className="flex flex-col gap-2 min-h-0">
          <Breadth buyPct={s.buy_pct} sellPct={s.sell_pct} rawHz={s.raw_hz} accel={s.accel} />
          <Ticker ticker={s.ticker} />
        </div>
        <ScannerBoards highs={s.new_highs} lows={s.new_lows} onFocus={(sym) => cmd('chart', sym)} />
        <div className="flex flex-col gap-2 min-h-0">
          <FocusChart candles={s.focus.candles} />
          <QuotePanel
            symbol={s.focus.symbol}
            asset={s.focus.asset}
            last={s.focus.last}
            pct={s.focus.pct}
            hi={s.focus.hi}
            lo={s.focus.lo}
            fundamentals={s.focus.fundamentals}
          />
          <DepthLadder symbol={s.focus.symbol} view={s.focus.depth} />
          <Watchlist rows={s.watchlist} />
        </div>
      </div>
      {!connected && (
        <div className="text-center text-amber-500 text-xs py-1 border-t border-neutral-800">
          reconnecting…
        </div>
      )}
      <CommandBar onSubmit={cmd} />
    </div>
  )
}
