import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ScannerBoards } from '../ScannerBoards'

describe('ScannerBoards', () => {
  it('renders rows and fires focus on click', () => {
    const onFocus = vi.fn()
    render(
      <ScannerBoards
        highs={[['AAPL', 122, 228.6, 1.9]]}
        lows={[['DKNG', 28, 39.2, -2.1]]}
        onFocus={onFocus}
      />,
    )
    fireEvent.click(screen.getByText('AAPL'))
    expect(onFocus).toHaveBeenCalledWith('AAPL')
  })
})
