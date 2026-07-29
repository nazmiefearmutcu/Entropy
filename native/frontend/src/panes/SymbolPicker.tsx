import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import type { SymbolRow } from '../contract'
import { addWatch, getWatchlist, removeWatch, searchSymbols, setFocus } from '../api'
import { usePort } from '../port'
import { reportAck } from '../toast'
import type { PickerMode } from '../ui-state'
import { Chip, Kbd } from '../ui/controls'
import { Icon } from '../ui/Icon'

const DEBOUNCE_MS = 160

function assetTone(cls: string): 'info' | 'accent' | 'neutral' {
  const c = cls.toUpperCase()
  if (c === 'EQUITY') return 'info'
  if (c === 'CRYPTO') return 'accent'
  return 'neutral'
}

/**
 * Command-palette symbol search. This is the answer to "you cannot change which
 * symbol the chart shows": Cmd+K anywhere, type, arrow, Enter.
 */
export function SymbolPicker({ mode, onClose }: { mode: PickerMode; onClose: () => void }) {
  const port = usePort()
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<SymbolRow[]>([])
  /** Which query `rows` belongs to; guards Enter against acting on stale hits. */
  const [rowsFor, setRowsFor] = useState<string | null>(null)
  const [index, setIndex] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const seq = useRef(0)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    const id = seq.current + 1
    seq.current = id
    const q = query.trim()
    const timer = setTimeout(() => {
      setLoading(true)
      const run = q
        ? searchSymbols(port, q, 40)
        : getWatchlist(port).then((w) =>
            // WatchlistEntry is a SymbolRow minus `watched` — spread rather
            // than list the fields, so a new display field cannot go missing
            // on this path while the search path has it.
            w.map((e) => ({ ...e, watched: true })),
          )
      run
        .then((r) => {
          if (seq.current !== id) return
          setRows(r)
          setRowsFor(q)
          setIndex(0)
          setError(null)
        })
        .catch((e: unknown) => {
          if (seq.current !== id) return
          setRows([])
          setRowsFor(q)
          setError(e instanceof Error ? e.message : String(e))
        })
        .finally(() => {
          if (seq.current === id) setLoading(false)
        })
    }, DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [query, port])

  const stale = rowsFor !== query.trim()

  const choose = useCallback(
    async (row: SymbolRow) => {
      setPending(row.symbol)
      const ack =
        mode === 'watch' ? await addWatch(port, row.symbol) : await setFocus(port, row.symbol)
      setPending(null)
      if (reportAck(ack)) onClose()
    },
    [mode, onClose, port],
  )

  const toggleWatch = useCallback(
    async (row: SymbolRow) => {
      setPending(row.symbol)
      const ack = row.watched
        ? await removeWatch(port, row.symbol)
        : await addWatch(port, row.symbol)
      setPending(null)
      if (reportAck(ack)) {
        setRows((prev) =>
          prev.map((r) => (r.symbol === row.symbol ? { ...r, watched: !row.watched } : r)),
        )
      }
    },
    [port],
  )

  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setIndex((i) => Math.min(rows.length - 1, i + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setIndex((i) => Math.max(0, i - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      // Never act on hits that belong to an earlier query.
      if (stale) return
      const row = rows[index]
      if (!row) return
      if (e.metaKey || e.ctrlKey) void toggleWatch(row)
      else void choose(row)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${index}"]`)
    if (typeof el?.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' })
  }, [index])

  const heading = mode === 'watch' ? 'Add to watchlist' : 'Focus symbol'
  const placeholder = useMemo(
    () =>
      mode === 'watch'
        ? 'Search a symbol to follow — AAPL, BTC/USDT, name…'
        : 'Search symbols — AAPL, BTC/USDT, company name…',
    [mode],
  )

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-black/65 px-4 pt-[12vh]"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-label={heading}
        className="modal-in flex max-h-[62vh] w-full max-w-[660px] flex-col overflow-hidden rounded-[4px] bg-panel shadow-pop ring-1 ring-line-strong"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <Icon name="search" size={15} className="text-ink-mute" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            aria-label="Symbol search"
            className="min-w-0 flex-1 bg-transparent font-mono text-md text-ink outline-none placeholder:font-ui placeholder:text-sm placeholder:text-ink-faint"
          />
          <Chip tone={mode === 'watch' ? 'accent' : 'info'}>{heading}</Chip>
        </div>

        <div
          ref={listRef}
          className={`min-h-0 flex-1 overflow-y-auto py-1 ${stale ? 'opacity-50' : ''}`}
        >
          {error && (
            <p className="px-3 py-3 text-sm text-down">
              Symbol catalog unavailable: {error}
            </p>
          )}
          {!error && rows.length === 0 && (
            <p className="px-3 py-3 text-sm text-ink-mute">
              {loading || stale
                ? 'searching…'
                : query.trim()
                  ? `Nothing matches "${query.trim()}".`
                  : 'Your watchlist is empty. Type to search the catalog.'}
            </p>
          )}
          {rows.map((row, i) => {
            const active = i === index
            return (
              <div
                key={`${row.venue}:${row.symbol}`}
                data-idx={i}
                className={`flex items-center gap-2 border-l-2 pl-2.5 pr-1.5 ${
                  active ? 'border-accent bg-accent-soft' : 'border-transparent hover:bg-hover'
                }`}
              >
                <button
                  type="button"
                  onMouseEnter={() => setIndex(i)}
                  onClick={() => {
                    if (!stale) void choose(row)
                  }}
                  disabled={pending === row.symbol}
                  className="flex min-w-0 flex-1 items-center gap-3 py-1.5 text-left disabled:opacity-50"
                >
                  {/* Terminal row: the exchange's OWN ticker leads, the
                      instrument is spelled out, the venue is a trailing badge.
                      Showing the canonical id here meant every crypto row began
                      with the same nine characters and truncated before the
                      part that identifies it. */}
                  <span
                    className={`w-[104px] shrink-0 truncate font-mono text-sm font-semibold ${
                      active ? 'text-accent' : 'text-ink'
                    }`}
                    title={row.symbol}
                  >
                    {row.ticker || row.symbol}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-ink-dim">
                    {row.name || '—'}
                  </span>
                  <Chip tone={assetTone(row.asset_class)}>{row.asset_class}</Chip>
                  <span className="w-[86px] shrink-0 truncate text-right font-mono text-micro text-ink-faint">
                    {row.exchange || row.venue}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={
                    row.watched
                      ? `Unfollow ${row.ticker || row.symbol}`
                      : `Follow ${row.ticker || row.symbol}`
                  }
                  title={row.watched ? 'Remove from watchlist' : 'Add to watchlist'}
                  disabled={pending === row.symbol}
                  onClick={() => void toggleWatch(row)}
                  className={`shrink-0 rounded-[2px] p-1 disabled:opacity-40 ${
                    row.watched ? 'text-accent' : 'text-ink-faint hover:text-ink-dim'
                  }`}
                >
                  <Icon name={row.watched ? 'star-filled' : 'star'} size={14} />
                </button>
              </div>
            )
          })}
        </div>

        <div className="flex items-center gap-3 border-t border-line bg-raised px-3 py-1.5 text-micro text-ink-mute">
          <span className="flex items-center gap-1">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> move
          </span>
          <span className="flex items-center gap-1">
            <Kbd>↵</Kbd> {mode === 'watch' ? 'follow' : 'focus chart'}
          </span>
          <span className="flex items-center gap-1">
            <Kbd>⌘↵</Kbd> toggle watchlist
          </span>
          <span className="ml-auto flex items-center gap-1">
            <Kbd>esc</Kbd> close
          </span>
        </div>
      </div>
    </div>
  )
}
