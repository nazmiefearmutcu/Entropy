import { useEffect, useState } from 'react'
import { clock as fmtClock } from '../format'
import { useChrome, useFeeds, useFocusSymbol } from '../store'
import { openPicker, openSettings } from '../ui-state'
import { Dot, IconButton, Kbd } from '../ui/controls'
import { Icon } from '../ui/Icon'

function feedTone(state: string): 'up' | 'down' | 'accent' | 'info' | 'mute' {
  if (state === 'live') return 'up'
  if (state === 'error') return 'down'
  if (state === 'sim') return 'accent'
  if (state === 'connecting') return 'info'
  return 'mute'
}

/** One venue class and the honest state of its feed. */
export function FeedChip({ name, state, detail }: { name: string; state: string; detail?: string }) {
  const tone = feedTone(state)
  const label = state || 'off'
  return (
    <span
      title={detail ? `${name}: ${label} — ${detail}` : `${name}: ${label}`}
      className="inline-flex h-[22px] items-center gap-1.5 rounded-[3px] bg-raised px-2"
    >
      <Dot tone={tone} pulse={state === 'connecting'} />
      <span className="text-micro font-semibold uppercase tracking-label text-ink-dim">{name}</span>
      <span
        className={`font-mono text-micro uppercase ${
          tone === 'up'
            ? 'text-up'
            : tone === 'down'
              ? 'text-down'
              : tone === 'accent'
                ? 'text-accent'
                : 'text-ink-mute'
        }`}
      >
        {label}
      </span>
    </span>
  )
}

function Wordmark() {
  return (
    <div className="flex items-center gap-2 pr-1">
      <span className="flex h-[22px] w-[22px] items-center justify-center rounded-[3px] bg-accent text-black">
        <Icon name="mark" size={13} />
      </span>
      <span className="text-sm font-bold uppercase tracking-mark text-ink">Entropy</span>
    </div>
  )
}

export function TopBar() {
  const { marketStatus } = useChrome()
  const feeds = useFeeds()
  const symbol = useFocusSymbol()
  const [now, setNow] = useState(() => fmtClock())

  useEffect(() => {
    const id = setInterval(() => setNow(fmtClock()), 1000)
    return () => clearInterval(id)
  }, [])

  const open = marketStatus.toLowerCase() === 'open'

  return (
    <header className="flex h-11 shrink-0 items-center gap-3 bg-panel px-3">
      <Wordmark />

      <span className="h-5 w-px bg-line" />

      <span
        className="inline-flex items-center gap-1.5"
        title={`US equity session: ${marketStatus || 'unknown'}`}
      >
        <Dot tone={open ? 'up' : 'mute'} />
        <span className="text-micro uppercase tracking-label text-ink-mute">NYSE</span>
        <span className={`font-mono text-xs uppercase ${open ? 'text-up' : 'text-ink-dim'}`}>
          {marketStatus || 'unknown'}
        </span>
      </span>

      <FeedChip name="EQ" state={feeds.equities} detail={feeds.detail} />
      <FeedChip name="CR" state={feeds.crypto} detail={feeds.detail} />

      <button
        type="button"
        onClick={() => openPicker('focus')}
        title="Search symbols and change the chart focus"
        className="group ml-auto flex h-[26px] w-[300px] items-center gap-2 rounded-[3px] bg-sunken px-2 text-left transition-colors duration-150 hover:bg-raised"
      >
        <Icon name="search" size={13} className="text-ink-mute group-hover:text-accent" />
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-dim">
          {symbol ? symbol : 'Search symbols'}
        </span>
        <span className="flex shrink-0 items-center gap-0.5">
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
        </span>
      </button>

      <span className="font-mono tnum text-sm text-ink-dim" title="Local time">
        {now}
      </span>

      <IconButton icon="gear" label="Settings (Cmd+,)" onClick={() => openSettings('general')} />
    </header>
  )
}
