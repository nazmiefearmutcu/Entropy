/**
 * Typed REST client for the Entropy sidecar.
 *
 * Every mutation resolves to an `ApiAck`. Transport failures are folded into
 * `{ ok: false, problems: [...] }` rather than thrown, so call sites always have
 * something honest to show the user instead of a silent no-op.
 */
import type {
  ApiAck,
  BotAction,
  MetaResponse,
  SettingsPatch,
  SettingsPayload,
  SymbolRow,
  WatchlistEntry,
} from './contract'

export function apiBase(port: number): string {
  return `http://127.0.0.1:${port}`
}

async function readAck(res: Response): Promise<ApiAck> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  const obj = (body ?? {}) as Partial<ApiAck>
  if (!res.ok) {
    return {
      ok: false,
      message: obj.message ?? `${res.status} ${res.statusText}`,
      problems: obj.problems ?? [`sidecar returned HTTP ${res.status}`],
    }
  }
  return {
    ok: obj.ok !== false,
    message: obj.message ?? '',
    problems: obj.problems ?? [],
  }
}

function transportFailure(action: string, err: unknown): ApiAck {
  const detail = err instanceof Error ? err.message : String(err)
  return {
    ok: false,
    message: `${action} failed: sidecar unreachable`,
    problems: [detail],
  }
}

async function mutate(
  port: number,
  path: string,
  action: string,
  init: RequestInit = {},
): Promise<ApiAck> {
  try {
    const res = await fetch(`${apiBase(port)}${path}`, {
      headers: { 'content-type': 'application/json' },
      ...init,
    })
    return await readAck(res)
  } catch (err) {
    return transportFailure(action, err)
  }
}

async function query<T>(port: number, path: string): Promise<T> {
  const res = await fetch(`${apiBase(port)}${path}`, { headers: { accept: 'application/json' } })
  if (!res.ok) throw new Error(`GET ${path} -> HTTP ${res.status}`)
  return (await res.json()) as T
}

/* ---------------------------------------------------------------- reads */

export function getMeta(port: number): Promise<MetaResponse> {
  return query<MetaResponse>(port, '/api/meta')
}

export function getSettings(port: number): Promise<SettingsPayload> {
  return query<SettingsPayload>(port, '/api/settings')
}

export function getWatchlist(port: number): Promise<WatchlistEntry[]> {
  return query<WatchlistEntry[]>(port, '/api/watchlist')
}

export function searchSymbols(port: number, q: string, limit = 40): Promise<SymbolRow[]> {
  const qs = new URLSearchParams({ q, limit: String(limit) })
  return query<SymbolRow[]>(port, `/api/symbols?${qs.toString()}`)
}

/* ------------------------------------------------------------- mutations */

export function putSettings(port: number, patch: SettingsPatch): Promise<ApiAck> {
  return mutate(port, '/api/settings', 'save settings', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

export function setFocus(port: number, symbol: string): Promise<ApiAck> {
  return mutate(port, '/api/focus', 'focus symbol', {
    method: 'POST',
    body: JSON.stringify({ symbol }),
  })
}

export function addWatch(port: number, symbol: string): Promise<ApiAck> {
  return mutate(port, '/api/watchlist', 'add to watchlist', {
    method: 'POST',
    body: JSON.stringify({ symbol }),
  })
}

export function removeWatch(port: number, symbol: string): Promise<ApiAck> {
  return mutate(port, `/api/watchlist/${encodeURIComponent(symbol)}`, 'remove from watchlist', {
    method: 'DELETE',
  })
}

export function botAction(port: number, action: BotAction): Promise<ApiAck> {
  return mutate(port, `/api/bot/${action}`, `bot ${action}`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export function postCommand(port: number, verb: string, arg = ''): Promise<ApiAck> {
  return mutate(port, '/api/command', `command ${verb}`, {
    method: 'POST',
    body: JSON.stringify({ verb, arg }),
  })
}
