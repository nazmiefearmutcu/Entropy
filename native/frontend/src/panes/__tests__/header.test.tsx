import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Header } from '../Header'

describe('Header', () => {
  it('renders market status, source and indices', () => {
    render(
      <Header marketStatus="open" source="live" indices={[['SPY', 559.2, 0.4]]} clock="09:41:22" />,
    )
    expect(screen.getByText(/NYSE open/i)).toBeTruthy()
    expect(screen.getByText(/source: live/i)).toBeTruthy()
    expect(screen.getByText(/SPY/)).toBeTruthy()
  })
})
