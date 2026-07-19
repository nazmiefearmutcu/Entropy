import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { DepthLadder } from '../DepthLadder'

const view = {
  basis: 'yahoo_1m_vap',
  is_synthetic: true,
  reference_price: 100,
  bids: [
    [99, 5] as [number, number],
    [98, 3] as [number, number],
  ],
  asks: [
    [101, 4] as [number, number],
    [102, 8] as [number, number],
  ],
}

describe('DepthLadder', () => {
  it('renders SYNTH badge and DOM ordering', () => {
    render(<DepthLadder symbol="AAPL" view={view} />)
    const text = document.body.textContent || ''
    expect(text).toContain('SYNTH·yahoo_1m_vap')
    expect(text).toContain('rel.liq')
    // ask 102 renders above ask 101 (higher in the DOM)
    const i102 = text.indexOf('102.00')
    const i101 = text.indexOf('101.00')
    expect(i102).toBeGreaterThanOrEqual(0)
    expect(i102).toBeLessThan(i101)
  })

  it('shows placeholder when view is null', () => {
    render(<DepthLadder symbol="AAPL" view={null} />)
    expect(document.body.textContent || '').toContain('DEPTH AAPL')
  })
})
