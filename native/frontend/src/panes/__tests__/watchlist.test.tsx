import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { WatchlistPanel } from '../Watchlist'
import { makeSnapshot } from '../../mock'
import { resetStore, setSnap } from '../../store'
import { clearToasts } from '../../toast'
import { getUi } from '../../ui-state'
import { renderWithPort, stubFetch, type Call } from '../../__tests__/helpers'

let calls: Call[]
const realFetch = globalThis.fetch

beforeEach(() => {
  clearToasts()
  resetStore()
  calls = stubFetch([{ match: '/api/watchlist/', respond: { ok: true, message: '', problems: [] } }])
})

afterEach(() => {
  globalThis.fetch = realFetch
  resetStore()
})

describe('WatchlistPanel', () => {
  it('renders followed symbols with a percentage and a remove control', async () => {
    setSnap(makeSnapshot(0))
    renderWithPort(<WatchlistPanel onFocus={() => undefined} />)

    // Rows lead with the exchange's own ticker plus a venue badge — the
    // canonical "binance-spot:BTCUSDT" is never rendered, it only keys the row
    // and rides in the delete path.
    expect(screen.getByText('AAPL')).toBeTruthy()
    expect(screen.getByText('BTCUSDT')).toBeTruthy()
    expect(screen.getByText('BINANCE')).toBeTruthy()
    expect(screen.queryByText('binance-spot:BTCUSDT')).toBeNull()

    fireEvent.click(screen.getByLabelText('Remove BTCUSDT from watchlist'))

    await waitFor(() => {
      const del = calls.find((c) => c.method === 'DELETE')
      expect(del).toBeTruthy()
      // ':' must be percent-encoded in the path segment
      expect(del?.url.endsWith('/api/watchlist/binance-spot%3ABTCUSDT')).toBe(true)
    })
  })

  it('focuses the clicked row', () => {
    setSnap(makeSnapshot(0))
    const seen: string[] = []
    renderWithPort(<WatchlistPanel onFocus={(s) => seen.push(s)} />)
    fireEvent.click(screen.getByTitle('Apple Inc. — US'))
    // the CANONICAL id is what gets focused, whatever the row displayed
    expect(seen).toEqual(['AAPL'])
  })

  it('offers an add affordance when the list is empty', () => {
    setSnap(makeSnapshot(0, { watchlist: [] }))
    renderWithPort(<WatchlistPanel onFocus={() => undefined} />)

    expect(screen.getByText('No symbols followed yet')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Add symbol' }))
    expect(getUi().picker).toBe('watch')
  })
})
