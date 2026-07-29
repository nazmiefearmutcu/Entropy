import type { ReactNode } from 'react'

/**
 * A workspace panel. Panels are lifted surfaces, not outlined boxes — structure
 * comes from the 1px base-coloured gaps the workspace grid leaves between them.
 */
export function Panel({
  children,
  className = '',
  tone = 'panel',
}: {
  children: ReactNode
  className?: string
  tone?: 'panel' | 'sunken'
}) {
  const bg = tone === 'sunken' ? 'bg-sunken' : 'bg-panel'
  return <section className={`flex min-h-0 flex-col ${bg} ${className}`}>{children}</section>
}

/**
 * Supporting panels get a quiet head bar. The chart and the bot dock do not use
 * this — they carry their own, louder headers, which is what makes them read as
 * primary.
 */
export function PanelHead({
  title,
  accent,
  meta,
  actions,
}: {
  title: string
  accent?: 'up' | 'down' | 'accent' | 'none'
  meta?: ReactNode
  actions?: ReactNode
}) {
  const bar =
    accent === 'up'
      ? 'before:bg-up'
      : accent === 'down'
        ? 'before:bg-down'
        : accent === 'accent'
          ? 'before:bg-accent'
          : 'before:bg-transparent'
  return (
    <header
      className={`relative flex h-[26px] shrink-0 items-center gap-2 bg-raised pl-2.5 pr-1.5 before:absolute before:left-0 before:top-0 before:h-full before:w-[2px] ${bar}`}
    >
      <h2 className="text-micro font-semibold uppercase tracking-label text-ink-dim">{title}</h2>
      {meta && <div className="min-w-0 flex-1 truncate text-micro text-ink-mute">{meta}</div>}
      <div className="ml-auto flex items-center gap-1">{actions}</div>
    </header>
  )
}

/** Placeholder that states what is missing and how to fix it — never a bare dash. */
export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-6 text-center">
      <p className="text-sm text-ink-dim">{title}</p>
      {hint && <p className="max-w-[34ch] text-xs leading-relaxed text-ink-mute">{hint}</p>}
      {action}
    </div>
  )
}
