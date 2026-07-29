import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// lightweight-charts needs a real canvas + ResizeObserver; the layout test only
// cares that the hero region mounts, so the library is stubbed.
vi.mock('lightweight-charts', () => {
  const series = {
    setData: vi.fn(),
    update: vi.fn(),
    priceScale: () => ({ applyOptions: vi.fn() }),
    applyOptions: vi.fn(),
  }
  return {
    ColorType: { Solid: 'solid' },
    CrosshairMode: { Normal: 0 },
    createChart: () => ({
      addCandlestickSeries: () => series,
      addLineSeries: () => series,
      addHistogramSeries: () => series,
      applyOptions: vi.fn(),
      remove: vi.fn(),
    }),
  }
})

const { default: App } = await import('../App')
const { resetConfig, resetStore } = await import('../store')

const realFetch = globalThis.fetch

beforeEach(() => {
  resetStore()
  resetConfig()
  window.history.replaceState({}, '', '/?mock=1')
  vi.useFakeTimers({ shouldAdvanceTime: false })
})

afterEach(() => {
  vi.useRealTimers()
  globalThis.fetch = realFetch
  resetStore()
  resetConfig()
  window.history.replaceState({}, '', '/')
})

describe('App shell in mock mode', () => {
  it('renders every major region', () => {
    render(<App />)

    // Chrome
    expect(screen.getByText('Entropy')).toBeTruthy()
    expect(screen.getByTitle('Local time')).toBeTruthy()
    expect(screen.getByLabelText('Settings (Cmd+,)')).toBeTruthy()

    // Symbol search affordance — the answer to "cannot change the chart symbol"
    expect(screen.getByTitle('Search symbols and change the chart focus')).toBeTruthy()

    // Workspace regions
    expect(screen.getByRole('complementary', { name: 'Scanner' })).toBeTruthy()
    expect(screen.getByRole('main', { name: 'Chart' })).toBeTruthy()
    expect(screen.getByRole('complementary', { name: 'Watchlist and depth' })).toBeTruthy()
    expect(screen.getByRole('region', { name: 'Trading bot' })).toBeTruthy()

    // Cadence controls are visible without opening anything
    expect(screen.getByRole('radiogroup', { name: 'Chart candle interval' })).toBeTruthy()
    expect(screen.getByLabelText('Scanner timeframe')).toBeTruthy()

    // Panels
    expect(screen.getByText('New highs')).toBeTruthy()
    expect(screen.getByText('New lows')).toBeTruthy()
    expect(screen.getByText('Watchlist')).toBeTruthy()
    expect(screen.getByText('Breadth')).toBeTruthy()

    // Resizable regions
    expect(screen.getByRole('separator', { name: 'Resize scanner rail' })).toBeTruthy()
    expect(screen.getByRole('separator', { name: 'Resize bot panel' })).toBeTruthy()
  })

  it('shows an honest boot state before the first frame', () => {
    window.history.replaceState({}, '', '/')
    globalThis.fetch = (() => Promise.reject(new Error('no sidecar'))) as unknown as typeof fetch
    render(<App />)
    expect(screen.getByText(/connecting to the sidecar/i)).toBeTruthy()
  })
})
