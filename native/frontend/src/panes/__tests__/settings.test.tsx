import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { SettingsDrawer } from '../SettingsDrawer'
import { MOCK_APP_CONFIG, MOCK_BOT_CONFIG, MOCK_META, makeSnapshot } from '../../mock'
import { resetConfig, resetStore, setConfig, setSnap } from '../../store'
import { clearToasts } from '../../toast'
import { renderWithPort, stubFetch, type Call } from '../../__tests__/helpers'

const PAYLOAD = { app: MOCK_APP_CONFIG, bot: MOCK_BOT_CONFIG }
const REJECT = {
  ok: false,
  message: 'settings rejected',
  problems: ["bot: Expected 'bool' for warmup", 'app.timeframe: 3m is not a known timeframe'],
}

let calls: Call[]
const realFetch = globalThis.fetch

beforeEach(() => {
  clearToasts()
  resetStore()
  resetConfig()
  setSnap(makeSnapshot(0))
  setConfig({ meta: MOCK_META, settings: PAYLOAD, error: null, loading: false })
})

afterEach(() => {
  globalThis.fetch = realFetch
  resetStore()
  resetConfig()
})

describe('SettingsDrawer', () => {
  it('surfaces every problem verbatim and says nothing was applied', async () => {
    calls = stubFetch([
      { match: '/api/settings', method: 'GET', respond: PAYLOAD },
      { match: '/api/meta', respond: MOCK_META },
      { match: '/api/settings', method: 'PUT', respond: REJECT },
    ])

    renderWithPort(<SettingsDrawer tab="scanner" onClose={vi.fn()} />)

    const select = await screen.findByLabelText('Timeframe')
    fireEvent.change(select, { target: { value: '1h' } })

    await waitFor(() => {
      expect(screen.getByText(/nothing was applied/i)).toBeTruthy()
      expect(screen.getByText("bot: Expected 'bool' for warmup")).toBeTruthy()
      expect(screen.getByText('app.timeframe: 3m is not a known timeframe')).toBeTruthy()
    })

    const put = calls.find((c) => c.method === 'PUT')
    expect(put?.body).toEqual({ app: { timeframe: '1h' } })
  })

  it('sends a nested partial for a consensus field', async () => {
    calls = stubFetch([
      { match: '/api/settings', method: 'GET', respond: PAYLOAD },
      { match: '/api/meta', respond: MOCK_META },
      { match: '/api/settings', method: 'PUT', respond: { ok: true, message: '', problems: [] } },
    ])

    renderWithPort(<SettingsDrawer tab="bot" onClose={vi.fn()} />)

    fireEvent.click(await screen.findByText('Advanced — signal logic'))
    fireEvent.change(await screen.findByLabelText('Vote mode'), { target: { value: 'trend' } })

    await waitFor(() => {
      const put = calls.find((c) => c.method === 'PUT')
      expect(put?.body).toEqual({ bot: { consensus: { vote_mode: 'trend' } } })
    })
  })

  it('labels the legacy vote mode as the old broken mapping', async () => {
    stubFetch([
      {
        match: '/api/settings',
        method: 'GET',
        respond: {
          app: MOCK_APP_CONFIG,
          bot: { ...MOCK_BOT_CONFIG, consensus: { ...MOCK_BOT_CONFIG.consensus, vote_mode: 'legacy' } },
        },
      },
      { match: '/api/meta', respond: MOCK_META },
    ])

    renderWithPort(<SettingsDrawer tab="bot" onClose={vi.fn()} />)
    fireEvent.click(await screen.findByText('Advanced — signal logic'))

    await waitFor(() => expect(screen.getAllByText(/trend-blind/i).length).toBeGreaterThan(0))
  })

  it('shows where the settings persist', async () => {
    stubFetch([
      { match: '/api/settings', method: 'GET', respond: PAYLOAD },
      { match: '/api/meta', respond: MOCK_META },
    ])
    renderWithPort(<SettingsDrawer tab="general" onClose={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getAllByTitle(MOCK_META.settings_path).length).toBeGreaterThan(0),
    )
  })

  it('keys the risk-profile options on the value BotConfig actually stores', async () => {
    // Regression: /api/meta names profiles "Frosty"/"Medium"/"Extreme" but
    // BotConfig stores the lowercase key. Keying the options on the display name
    // meant the stored value matched nothing, so the select injected it as a
    // duplicate fourth entry and the description lookup found nothing.
    renderWithPort(<SettingsDrawer tab="bot" onClose={vi.fn()} />)
    const select = (await screen.findByLabelText(/risk profile/i)) as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['frosty', 'medium', 'extreme'])
    expect([...select.options].map((o) => o.textContent)).toEqual(['Frosty', 'Medium', 'Extreme'])
    expect(select.value).toBe('medium')
  })
})
