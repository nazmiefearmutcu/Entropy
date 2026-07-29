import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { BotDock } from '../BotDock'
import { makeSnapshot } from '../../mock'
import { resetConfig, resetStore, setSnap } from '../../store'
import { clearToasts } from '../../toast'
import { setBotOpen } from '../../ui-state'
import { renderWithPort, stubFetch, type Call } from '../../__tests__/helpers'

const OK = { ok: true, message: 'ok', problems: [] }

let calls: Call[]
const realFetch = globalThis.fetch

beforeEach(() => {
  clearToasts()
  resetStore()
  resetConfig()
  setBotOpen(true)
  calls = stubFetch([{ match: '/api/bot/', respond: OK }])
})

afterEach(() => {
  globalThis.fetch = realFetch
  resetStore()
})

function botCalls() {
  return calls.filter((c) => c.url.includes('/api/bot/')).map((c) => c.url.split('/api/bot/')[1])
}

describe('BotDock controls', () => {
  it('stops a running bot through /api/bot/stop', async () => {
    setSnap(makeSnapshot(0))
    renderWithPort(<BotDock />)

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(botCalls()).toContain('stop'))
    expect(calls[0].method).toBe('POST')
  })

  it('pauses a running bot through /api/bot/pause', async () => {
    setSnap(makeSnapshot(0))
    renderWithPort(<BotDock />)

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(botCalls()).toContain('pause'))
  })

  it('requires a confirmation step before halting', async () => {
    setSnap(makeSnapshot(0))
    renderWithPort(<BotDock />)

    fireEvent.click(screen.getByRole('button', { name: 'Emergency halt' }))
    expect(botCalls()).toEqual([])

    fireEvent.click(screen.getByRole('button', { name: 'Confirm halt' }))
    await waitFor(() => expect(botCalls()).toContain('halt'))
  })

  it('offers a working Start control when no bot session exists yet', async () => {
    setSnap(makeSnapshot(0, { bot: null }))
    renderWithPort(<BotDock />)

    expect(screen.getByText('Bot not started')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Start bot' }))
    await waitFor(() => expect(botCalls()).toContain('start'))
  })

  it('shows positions, strategy regimes and the reject log', () => {
    setSnap(makeSnapshot(0))
    renderWithPort(<BotDock />)

    expect(screen.getByText('AAPL')).toBeTruthy()
    expect(screen.getByText('LONG')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /Strategies/ }))
    expect(screen.getByText('trend')).toBeTruthy()
    expect(screen.getByText('+1')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /Rejects/ }))
    expect(screen.getByText(/min_participation 0.38/)).toBeTruthy()
  })

  it('marks live mode loudly', () => {
    const snap = makeSnapshot(0)
    setSnap({ ...snap, bot: { ...snap.bot!, mode: 'live' } })
    renderWithPort(<BotDock />)
    expect(screen.getByText('live money')).toBeTruthy()
  })
})
