import { useEffect, useRef, useState } from 'react'
import { Kbd } from '../ui/controls'

const HINTS = [
  'chart AAPL',
  'watch BTC/USDT',
  'tf 15m',
  'interval 1m',
  'bot start',
  'source live',
  'depth',
]

/**
 * The power-user path, kept intact. Everything reachable here is also reachable
 * from a visible control — this is the accelerator, not the only door.
 */
export function CommandBar({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (verb: string, arg: string) => void
}) {
  const [text, setText] = useState('')
  const [history, setHistory] = useState<string[]>([])
  const [cursor, setCursor] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setCursor(-1)
      inputRef.current?.focus()
    } else {
      setText('')
    }
  }, [open])

  if (!open) return null

  const submit = () => {
    const raw = text.trim()
    const parts = raw.split(/\s+/)
    const verb = (parts[0] || '').toLowerCase()
    let arg = parts.slice(1).join(' ')
    if (arg && !arg.includes(':') && !arg.includes('/')) arg = arg.toUpperCase()
    if (verb) {
      onSubmit(verb, arg)
      setHistory((h) => [raw, ...h.filter((x) => x !== raw)].slice(0, 25))
    }
    setText('')
    onClose()
  }

  return (
    <div className="flex shrink-0 items-center gap-2 border-t border-accent/40 bg-sunken px-3 py-1.5">
      <span className="font-mono text-md font-bold text-accent">:</span>
      <input
        ref={inputRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        aria-label="Command"
        placeholder="chart AAPL · tf 15m · interval 1m · bot start"
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit()
          else if (e.key === 'Escape') {
            e.preventDefault()
            onClose()
          } else if (e.key === 'ArrowUp' && history.length > 0) {
            e.preventDefault()
            const next = Math.min(history.length - 1, cursor + 1)
            setCursor(next)
            setText(history[next])
          } else if (e.key === 'ArrowDown') {
            e.preventDefault()
            const next = cursor - 1
            setCursor(next)
            setText(next < 0 ? '' : history[next])
          }
        }}
        className="min-w-0 flex-1 bg-transparent font-mono text-sm text-ink outline-none placeholder:text-ink-faint"
      />
      <div className="hidden items-center gap-2 lg:flex">
        {HINTS.map((h) => (
          <button
            key={h}
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => setText(h)}
            className="font-mono text-micro text-ink-faint hover:text-accent"
          >
            {h}
          </button>
        ))}
      </div>
      <span className="flex shrink-0 items-center gap-1 text-micro text-ink-mute">
        <Kbd>↵</Kbd> run <Kbd>esc</Kbd> close
      </span>
    </div>
  )
}
