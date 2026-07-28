import type { ReactNode } from 'react'
import { useConfig, useBotSummary, useChrome, useConnected } from '../store'
import { truncateMiddle } from '../format'
import { Dot, Kbd } from '../ui/controls'
import { openSettings } from '../ui-state'

function Slot({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" title={title}>
      {children}
    </span>
  )
}

const LABEL = 'text-micro uppercase tracking-label text-ink-faint'

/** Chrome. Reports transport truth first, then scanner cadence, then the bot. */
export function StatusBar() {
  const connected = useConnected()
  const { source, rawHz, accel } = useChrome()
  const bot = useBotSummary()
  const { meta } = useConfig()

  const botState = !bot.present
    ? 'absent'
    : bot.halted
      ? 'halted'
      : bot.paused
        ? 'paused'
        : bot.running
          ? 'running'
          : 'stopped'

  return (
    <footer className="flex h-6 shrink-0 items-center gap-3 overflow-hidden bg-panel px-3 text-xs">
      <Slot title={connected ? 'Streaming from the sidecar' : 'Socket down — retrying with backoff'}>
        <Dot tone={connected ? 'up' : 'down'} pulse={!connected} />
        <span className={`font-mono uppercase ${connected ? 'text-up' : 'text-down'}`}>
          {connected ? 'stream' : 'reconnecting'}
        </span>
      </Slot>

      <span className="h-3 w-px bg-line" />

      <Slot title="Active market data source">
        <span className={LABEL}>src</span>
        <span className="font-mono text-ink-dim">{source || '–'}</span>
      </Slot>

      <Slot title="Raw tick rate reaching the scanner">
        <span className={LABEL}>rate</span>
        <span className="font-mono tnum text-ink-dim">{rawHz.toFixed(0)} Hz</span>
      </Slot>

      <Slot title="Tape acceleration regime">
        <span className={LABEL}>accel</span>
        <span className="font-mono text-ink-dim">{accel || '–'}</span>
      </Slot>

      <span className="h-3 w-px bg-line" />

      <Slot title="Bot engine state">
        <span className={LABEL}>bot</span>
        <span
          className={`font-mono uppercase ${
            botState === 'running'
              ? 'text-up'
              : botState === 'halted'
                ? 'text-down'
                : botState === 'paused'
                  ? 'text-accent'
                  : 'text-ink-mute'
          }`}
        >
          {botState}
        </span>
        {bot.present && <span className="font-mono tnum text-ink-faint">{bot.ticks} ticks</span>}
      </Slot>

      <div className="ml-auto flex items-center gap-3">
        {meta?.settings_path && (
          <button
            type="button"
            onClick={() => openSettings('general')}
            title={meta.settings_path}
            className="hidden font-mono text-micro text-ink-faint hover:text-ink-dim lg:inline"
          >
            {truncateMiddle(meta.settings_path, 40)}
          </button>
        )}
        <span className="hidden items-center gap-1.5 text-ink-faint md:inline-flex">
          <Kbd>⌘K</Kbd> symbol
          <Kbd>⌘,</Kbd> settings
          <Kbd>⌘B</Kbd> bot
          <Kbd>:</Kbd> command
        </span>
      </div>
    </footer>
  )
}
