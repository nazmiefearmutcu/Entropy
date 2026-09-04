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
    __SIDECAR_TOKEN__?: string
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

/**
 * Per-process sidecar auth token, injected by the Tauri shell next to the
 * port. Every /api route and the WS handshake must carry it. Dev escape
 * hatch: `localStorage.setItem('entropy_sidecar_token', <TOKEN= line>)` when
 * driving a manually-launched sidecar from the vite dev server.
 */
export function resolveToken(): string {
  if (window.__SIDECAR_TOKEN__) return window.__SIDECAR_TOKEN__
  try {
    return window.localStorage.getItem('entropy_sidecar_token') ?? ''
  } catch {
    return ''
  }
}
