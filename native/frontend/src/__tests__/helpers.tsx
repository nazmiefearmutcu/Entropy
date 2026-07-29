import type { ReactElement } from 'react'
import { render } from '@testing-library/react'
import { PortContext } from '../port'

export const TEST_PORT = 9911

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

export interface Call {
  url: string
  method: string
  body: unknown
}

/**
 * Records every request and answers from a routing table keyed by URL substring.
 * Nothing in the suite ever reaches a real sidecar.
 */
export function stubFetch(routes: { match: string; method?: string; respond: unknown }[]) {
  const calls: Call[] = []
  const impl = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown = null
    if (init?.body) {
      try {
        body = JSON.parse(String(init.body))
      } catch {
        body = String(init.body)
      }
    }
    calls.push({ url, method, body })
    const route = routes.find(
      (r) => url.includes(r.match) && (!r.method || r.method.toUpperCase() === method),
    )
    return Promise.resolve(json(route ? route.respond : { ok: true, message: '', problems: [] }))
  }
  globalThis.fetch = impl as unknown as typeof fetch
  return calls
}

export function renderWithPort(ui: ReactElement) {
  return render(<PortContext.Provider value={TEST_PORT}>{ui}</PortContext.Provider>)
}
