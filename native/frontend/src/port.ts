import { createContext, useContext } from 'react'

/**
 * Sidecar port, resolved once at boot. It is a context rather than a module
 * global so tests can mount a pane against a stub port without booting App.
 */
export const PortContext = createContext<number>(8000)

export function usePort(): number {
  return useContext(PortContext)
}

declare global {
  interface Window {
    __SIDECAR_PORT__?: number
  }
}

export function resolvePort(): number {
  const q = new URLSearchParams(location.search).get('port')
  if (q) {
    const n = parseInt(q, 10)
    if (Number.isFinite(n)) return n
  }
  return window.__SIDECAR_PORT__ ?? 8000
}
