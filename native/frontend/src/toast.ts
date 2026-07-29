/** Tiny toast bus. Anything that can fail reports through here. */
import { useSyncExternalStore } from 'react'
import type { ApiAck } from './contract'

export type ToastKind = 'ok' | 'error' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  title: string
  lines: string[]
  /** ms; 0 keeps it until dismissed. */
  ttl: number
}

let toasts: Toast[] = []
let nextId = 1
const subs = new Set<() => void>()

function emit() {
  for (const f of subs) f()
}

function subscribe(cb: () => void) {
  subs.add(cb)
  return () => {
    subs.delete(cb)
  }
}

export function pushToast(t: { kind: ToastKind; title: string; lines?: string[]; ttl?: number }): number {
  const id = nextId
  nextId += 1
  const ttl = t.ttl ?? (t.kind === 'error' ? 0 : 3200)
  toasts = [...toasts, { id, kind: t.kind, title: t.title, lines: t.lines ?? [], ttl }].slice(-5)
  emit()
  if (ttl > 0) setTimeout(() => dismissToast(id), ttl)
  return id
}

export function dismissToast(id: number) {
  const next = toasts.filter((t) => t.id !== id)
  if (next.length !== toasts.length) {
    toasts = next
    emit()
  }
}

export function clearToasts() {
  toasts = []
  emit()
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    subscribe,
    () => toasts,
    () => toasts,
  )
}

/**
 * Surface an acknowledgement. Failures always raise a sticky toast carrying the
 * sidecar's own `problems`; successes are silent unless `okTitle` is given.
 */
export function reportAck(ack: ApiAck, okTitle?: string): boolean {
  if (!ack.ok) {
    pushToast({
      kind: 'error',
      title: ack.message || 'Request rejected',
      lines: ack.problems,
    })
    return false
  }
  if (okTitle) pushToast({ kind: 'ok', title: okTitle, lines: ack.message ? [ack.message] : [] })
  return true
}
