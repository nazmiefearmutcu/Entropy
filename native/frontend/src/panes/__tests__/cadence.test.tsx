import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { CadenceControls } from '../IntervalControls'
import { MOCK_APP_CONFIG, MOCK_BOT_CONFIG, MOCK_META, makeSnapshot } from '../../mock'
import { resetConfig, resetStore, setConfig, setSnap } from '../../store'
import { clearToasts, useToasts } from '../../toast'
import { renderWithPort, stubFetch, type Call } from '../../__tests__/helpers'

const OK = { ok: true, message: 'saved', problems: [] }

let calls: Call[]
const realFetch = globalThis.fetch

function seed() {
  setSnap(makeSnapshot(0))
  setConfig({
    meta: MOCK_META,
    settings: { app: MOCK_APP_CONFIG, bot: MOCK_BOT_CONFIG },
    error: null,
    loading: false,
  })
}

beforeEach(() => {
  clearToasts()
  resetStore()
  resetConfig()
  seed()
  calls = stubFetch([{ match: '/api/settings', method: 'PUT', respond: OK }])
})

afterEach(() => {
  globalThis.fetch = realFetch
  resetStore()
  resetConfig()
})

describe('cadence controls', () => {
  it('sends only chart_interval when a candle width is picked', async () => {
    renderWithPort(<CadenceControls />)
    fireEvent.click(screen.getByRole('radio', { name: '5m' }))

    await waitFor(() => {
      const put = calls.find((c) => c.url.includes('/api/settings') && c.method === 'PUT')
      expect(put).toBeTruthy()
      expect(put?.body).toEqual({ app: { chart_interval: '5m' } })
    })
  })

  it('maps Follow to an empty chart_interval', async () => {
    renderWithPort(<CadenceControls />)
    fireEvent.click(screen.getByRole('radio', { name: '1m' }))
    await waitFor(() => expect(calls.length).toBe(1))
    fireEvent.click(screen.getByRole('radio', { name: 'Follow' }))

    await waitFor(() => {
      expect(calls[calls.length - 1].body).toEqual({ app: { chart_interval: '' } })
    })
  })

  it('sends the scanner timeframe on its own key, not the chart one', async () => {
    renderWithPort(<CadenceControls />)
    fireEvent.change(screen.getByLabelText('Scanner timeframe'), { target: { value: '1h' } })

    await waitFor(() => {
      const put = calls.find((c) => c.method === 'PUT')
      expect(put?.body).toEqual({ app: { timeframe: '1h' } })
    })
  })

  it('shows the active interval from the snapshot, not from local state', () => {
    renderWithPort(<CadenceControls />)
    // The mock snapshot carries chart_interval "" -> Follow is the selected option.
    expect(screen.getByRole('radio', { name: 'Follow' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('radio', { name: '5m' }).getAttribute('aria-checked')).toBe('false')
  })

  it('raises the sidecar problems when the write is rejected', async () => {
    globalThis.fetch = undefined as unknown as typeof fetch
    calls = stubFetch([
      {
        match: '/api/settings',
        method: 'PUT',
        respond: { ok: false, message: 'rejected', problems: ['app.chart_interval: unsupported'] },
      },
    ])
    const seen: string[] = []
    function Probe() {
      const toasts = useToasts()
      seen.length = 0
      toasts.forEach((t) => seen.push(...t.lines))
      return null
    }
    renderWithPort(
      <>
        <CadenceControls />
        <Probe />
      </>,
    )
    fireEvent.click(screen.getByRole('radio', { name: '15m' }))
    await waitFor(() => expect(seen).toContain('app.chart_interval: unsupported'))
  })
})

describe('the Follow option names the scanner timeframe', () => {
  it('reports the scanner timeframe, not the currently selected candle width', async () => {
    // Regression: the tooltip read the RESOLVED candle interval, so with 5m
    // candles under a 15m scan it promised "Follow the scanner timeframe (now
    // 5m)" — naming the very setting Follow would replace.
    setSnap(
      makeSnapshot(0, {
        settings: { ...makeSnapshot(0).settings, chart_interval: '5m', timeframe: '15m' },
        focus: { ...makeSnapshot(0).focus, interval: '5m', timeframe: '15m' },
      }),
    )
    renderWithPort(<CadenceControls />)
    await waitFor(() => {
      expect(screen.getByTitle(/Follow the scanner timeframe \(now 15m\)/)).toBeTruthy()
    })
  })
})
