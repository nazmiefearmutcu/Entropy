import { useCallback, useEffect, useState } from 'react'
import { getMeta, getSettings, postCommand, setFocus } from './api'
import { mockEnabled, startMock } from './mock'
import { PortContext, resolvePort } from './port'
import { setConfig, setConnected, setSnap, useConnected, useHasSnapshot } from './store'
import { reportAck } from './toast'
import {
  closePicker,
  closeSettings,
  openPicker,
  openSettings,
  setCommandOpen,
  resizeBot,
  resizeRailLeft,
  resizeRailRight,
  toggleBot,
  useUi,
} from './ui-state'
import { StreamClient } from './ws'
import { Resizer } from './ui/controls'
import { Toasts } from './ui/Toasts'
import { BotDock } from './panes/BotDock'
import { BreadthPanel } from './panes/Breadth'
import { ChartPanel } from './panes/ChartPanel'
import { CommandBar } from './panes/CommandBar'
import { DepthPanel } from './panes/DepthLadder'
import { QuoteStrip } from './panes/QuotePanel'
import { ScannerPanel } from './panes/ScannerBoards'
import { SettingsDrawer } from './panes/SettingsDrawer'
import { StatusBar } from './panes/StatusBar'
import { SymbolPicker } from './panes/SymbolPicker'
import { TickerPanel } from './panes/Ticker'
import { TopBar } from './panes/TopBar'
import { WatchlistPanel } from './panes/Watchlist'

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return (
    tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable === true
  )
}

function Booting({ port, connected }: { port: number; connected: boolean }) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-3 bg-base">
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-accent pulse-dot" />
        <span className="text-md font-semibold uppercase tracking-mark text-ink">Entropy</span>
      </div>
      <p className="text-sm text-ink-dim">
        {connected ? 'waiting for the first frame…' : `connecting to the sidecar on port ${port}…`}
      </p>
      <p className="max-w-[46ch] text-center text-xs leading-relaxed text-ink-mute">
        The window stays blank until the sidecar streams a snapshot. If this persists, the sidecar
        did not start — check the console log.
      </p>
    </div>
  )
}

/** The workspace: scanner rail, chart hero, watchlist rail, bot dock, chrome. */
function Workspace({ onFocus }: { onFocus: (symbol: string) => void }) {
  const ui = useUi()
  return (
    <div className="flex min-h-0 flex-1 gap-px bg-base">
      <aside
        className="flex min-h-0 shrink-0 flex-col gap-px bg-base"
        style={{ width: ui.railLeft }}
        aria-label="Scanner"
      >
        <BreadthPanel />
        <ScannerPanel onFocus={onFocus} />
        <TickerPanel onFocus={onFocus} />
      </aside>

      <Resizer axis="x" label="Resize scanner rail" onDrag={resizeRailLeft} />

      <main className="flex min-w-0 flex-1 flex-col gap-px bg-base" aria-label="Chart">
        <ChartPanel />
        <QuoteStrip />
      </main>

      <Resizer axis="x" label="Resize watchlist rail" onDrag={resizeRailRight} />

      <aside
        className="flex min-h-0 shrink-0 flex-col gap-px bg-base"
        style={{ width: ui.railRight }}
        aria-label="Watchlist and depth"
      >
        <WatchlistPanel onFocus={onFocus} />
        <DepthPanel />
      </aside>
    </div>
  )
}

export default function App() {
  const [port] = useState(resolvePort)
  const ui = useUi()
  const connected = useConnected()
  const ready = useHasSnapshot()

  useEffect(() => {
    if (mockEnabled()) return startMock()
    const client = new StreamClient(port, {
      onSnapshot: setSnap,
      onStatus: setConnected,
      onError: (m) => setConfig({ error: m }),
    })
    client.connect()
    return () => client.stop()
  }, [port])

  useEffect(() => {
    if (mockEnabled()) return
    getMeta(port)
      .then((meta) => setConfig({ meta }))
      .catch((e: unknown) =>
        setConfig({ error: e instanceof Error ? e.message : String(e) }),
      )
    getSettings(port)
      .then((settings) => setConfig({ settings }))
      .catch(() => undefined)
  }, [port])

  const focusSymbol = useCallback(
    (symbol: string) => {
      void setFocus(port, symbol).then((ack) => reportAck(ack))
    },
    [port],
  )

  const runCommand = useCallback(
    (verb: string, arg: string) => {
      void postCommand(port, verb, arg).then((ack) =>
        reportAck(ack, ack.ok ? ack.message || `${verb} ${arg}`.trim() : undefined),
      )
    },
    [port],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey
      if (meta && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        openPicker('focus')
      } else if (meta && e.key === ',') {
        e.preventDefault()
        openSettings('general')
      } else if (meta && e.key.toLowerCase() === 'b') {
        e.preventDefault()
        toggleBot()
      } else if (e.key === ':' && !isTyping(e.target)) {
        e.preventDefault()
        setCommandOpen(true)
      } else if (e.key === 'Escape') {
        setCommandOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const body = ready ? (
    <>
      <TopBar />
      <Workspace onFocus={focusSymbol} />
      <Resizer axis="y" label="Resize bot panel" onDrag={resizeBot} />
      <BotDock />
      <CommandBar
        open={ui.command}
        onClose={() => setCommandOpen(false)}
        onSubmit={runCommand}
      />
      <StatusBar />
    </>
  ) : (
    <Booting port={port} connected={connected} />
  )

  return (
    <PortContext.Provider value={port}>
      <div className="flex h-screen flex-col gap-px overflow-hidden bg-base">
        {body}
        {ui.picker && <SymbolPicker mode={ui.picker} onClose={closePicker} />}
        {ui.settings && <SettingsDrawer tab={ui.settings} onClose={closeSettings} />}
        <Toasts />
      </div>
    </PortContext.Provider>
  )
}
