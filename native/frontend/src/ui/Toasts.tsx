import { dismissToast, useToasts } from '../toast'
import { Icon } from './Icon'

const TONE = {
  error: { bar: 'bg-down', title: 'text-down' },
  ok: { bar: 'bg-up', title: 'text-up' },
  info: { bar: 'bg-info', title: 'text-info' },
} as const

/** Bottom-right stack. Errors stick until dismissed and always show `problems`. */
export function Toasts() {
  const toasts = useToasts()
  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none fixed bottom-9 right-3 z-50 flex w-[380px] flex-col gap-1.5">
      {toasts.map((t) => {
        const tone = TONE[t.kind]
        return (
          <div
            key={t.id}
            role="status"
            className="toast-in pointer-events-auto flex overflow-hidden rounded-[3px] bg-raised shadow-pop"
          >
            <div className={`w-[3px] shrink-0 ${tone.bar}`} />
            <div className="min-w-0 flex-1 px-2.5 py-2">
              <p className={`text-sm font-semibold ${tone.title}`}>{t.title}</p>
              {t.lines.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {t.lines.map((l, i) => (
                    <li key={i} className="break-words text-xs text-ink-dim">
                      {l}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismissToast(t.id)}
              className="shrink-0 px-2 text-ink-mute hover:text-ink"
            >
              <Icon name="close" size={12} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
