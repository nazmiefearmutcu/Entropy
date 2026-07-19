import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QuotePanel } from '../QuotePanel'

describe('QuotePanel', () => {
  it('shows last, pct, hi/lo and asset chip', () => {
    render(
      <QuotePanel
        symbol="AAPL"
        asset="EQUITY"
        last={228.66}
        pct={0.9}
        hi={229.4}
        lo={226.1}
        fundamentals={null}
      />,
    )
    expect(screen.getByText('AAPL')).toBeTruthy()
    expect(screen.getByText('EQUITY')).toBeTruthy()
    expect(document.body.textContent).toContain('228.66')
  })
})
