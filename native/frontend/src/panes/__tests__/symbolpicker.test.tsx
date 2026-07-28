import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { SymbolPicker } from '../SymbolPicker'
import { clearToasts } from '../../toast'
import { renderWithPort, stubFetch, type Call } from '../../__tests__/helpers'

// Shaped like the real GET /api/symbols: lowercase asset classes, canonical
// `venue:PAIR` ids, and the split display fields.
const SYMBOLS = [
  {
    symbol: 'AAPL', name: 'Apple Inc.', asset_class: 'equity', venue: 'us',
    ticker: 'AAPL', exchange: 'US', base: '', quote: '', watched: false,
  },
  {
    symbol: 'AAOI', name: 'Applied Optoelectronics', asset_class: 'equity', venue: 'us',
    ticker: 'AAOI', exchange: 'US', base: '', quote: '', watched: true,
  },
]

const CRYPTO = [
  {
    symbol: 'binance-spot:BTCUSDT', name: 'Bitcoin / TetherUS', asset_class: 'crypto',
    venue: 'binance-spot', ticker: 'BTCUSDT', exchange: 'BINANCE',
    base: 'BTC', quote: 'USDT', watched: false,
  },
]

const OK = { ok: true, message: '', problems: [] }

let calls: Call[]
const realFetch = globalThis.fetch

beforeEach(() => {
  clearToasts()
  calls = stubFetch([
    { match: '/api/symbols', respond: SYMBOLS },
    { match: '/api/watchlist', method: 'GET', respond: [] },
    { match: '/api/focus', respond: OK },
    { match: '/api/watchlist', method: 'POST', respond: OK },
  ])
})

afterEach(() => {
  globalThis.fetch = realFetch
})

describe('SymbolPicker', () => {
  it('searches and focuses the chosen symbol', async () => {
    const onClose = vi.fn()
    renderWithPort(<SymbolPicker mode="focus" onClose={onClose} />)

    fireEvent.change(screen.getByLabelText('Symbol search'), { target: { value: 'aa' } })

    await waitFor(() => expect(screen.getByText('AAPL')).toBeTruthy())
    expect(calls.some((c) => c.url.includes('/api/symbols') && c.url.includes('q=aa'))).toBe(true)

    fireEvent.click(screen.getByText('AAPL'))

    await waitFor(() => {
      const focus = calls.find((c) => c.url.includes('/api/focus'))
      expect(focus).toBeTruthy()
      expect(focus?.method).toBe('POST')
      expect(focus?.body).toEqual({ symbol: 'AAPL' })
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('moves the selection with the arrow keys and commits with Enter', async () => {
    const onClose = vi.fn()
    renderWithPort(<SymbolPicker mode="focus" onClose={onClose} />)
    const input = screen.getByLabelText('Symbol search')
    fireEvent.change(input, { target: { value: 'aa' } })
    await waitFor(() => expect(screen.getByText('AAOI')).toBeTruthy())

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      const focus = calls.find((c) => c.url.includes('/api/focus'))
      expect(focus?.body).toEqual({ symbol: 'AAOI' })
    })
  })

  it('adds to the watchlist when opened in watch mode', async () => {
    const onClose = vi.fn()
    renderWithPort(<SymbolPicker mode="watch" onClose={onClose} />)
    fireEvent.change(screen.getByLabelText('Symbol search'), { target: { value: 'aa' } })
    await waitFor(() => expect(screen.getByText('AAPL')).toBeTruthy())

    fireEvent.click(screen.getByText('AAPL'))

    await waitFor(() => {
      const post = calls.find((c) => c.url.endsWith('/api/watchlist') && c.method === 'POST')
      expect(post?.body).toEqual({ symbol: 'AAPL' })
    })
  })

  it('star toggle hits the watchlist endpoints without changing focus', async () => {
    renderWithPort(<SymbolPicker mode="focus" onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Symbol search'), { target: { value: 'aa' } })
    await waitFor(() => expect(screen.getByText('AAPL')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('Follow AAPL'))

    await waitFor(() => {
      const post = calls.find((c) => c.url.endsWith('/api/watchlist') && c.method === 'POST')
      expect(post?.body).toEqual({ symbol: 'AAPL' })
    })
    expect(calls.some((c) => c.url.includes('/api/focus'))).toBe(false)
  })
})

describe('SymbolPicker staleness guard', () => {
  it('ignores Enter while the results still belong to an earlier query', async () => {
    renderWithPort(<SymbolPicker mode="focus" onClose={vi.fn()} />)
    const input = screen.getByLabelText('Symbol search')

    fireEvent.change(input, { target: { value: 'aa' } })
    await waitFor(() => expect(screen.getByText('AAPL')).toBeTruthy())

    // New query typed; the old hits are still on screen but must not be committed.
    fireEvent.change(input, { target: { value: 'zzz' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(calls.some((c) => c.url.includes('/api/focus'))).toBe(false)
  })

  it('shows the exchange ticker, the instrument and the venue as three columns', async () => {
    // The canonical id is never rendered: "binance-spot:BTCUSDT" put nine
    // characters of venue in front of the part that identifies the pair, and
    // clipped to "binance-s…" in the process.
    calls = stubFetch([
      { match: '/api/symbols', respond: CRYPTO },
      { match: '/api/watchlist', method: 'GET', respond: [] },
      { match: '/api/focus', respond: OK },
    ])
    renderWithPort(<SymbolPicker mode="focus" onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Symbol search'), { target: { value: 'bitcoin' } })

    await waitFor(() => expect(screen.getByText('BTCUSDT')).toBeTruthy())
    expect(screen.getByText('Bitcoin / TetherUS')).toBeTruthy()
    expect(screen.getByText('BINANCE')).toBeTruthy()
    expect(screen.queryByText('binance-spot:BTCUSDT')).toBeNull()
  })

  it('focuses the CANONICAL id even though the row shows the ticker', async () => {
    calls = stubFetch([
      { match: '/api/symbols', respond: CRYPTO },
      { match: '/api/watchlist', method: 'GET', respond: [] },
      { match: '/api/focus', respond: OK },
    ])
    renderWithPort(<SymbolPicker mode="focus" onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Symbol search'), { target: { value: 'btc' } })

    await waitFor(() => expect(screen.getByText('BTCUSDT')).toBeTruthy())
    fireEvent.click(screen.getByText('BTCUSDT'))

    await waitFor(() => {
      const post = calls.find((c) => c.url.includes('/api/focus'))
      expect(post).toBeTruthy()
      expect(post?.body).toEqual({ symbol: 'binance-spot:BTCUSDT' })
    })
  })
})
