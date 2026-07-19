import { describe, it, expect } from 'vitest'
import { StreamClient } from '../ws'

class FakeWS {
  url: string
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  constructor(url: string) {
    this.url = url
    queueMicrotask(() => this.onopen?.())
  }
  send() {}
  close() {
    this.onclose?.()
  }
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }
}

describe('StreamClient', () => {
  it('parses snapshot frames to the handler', async () => {
    const got: unknown[] = []
    const client = new StreamClient(
      1234,
      { onSnapshot: (m) => got.push(m) },
      (u) => new FakeWS(u) as unknown as WebSocket,
    )
    client.connect()
    await new Promise((r) => setTimeout(r, 5))
    const fake = (client as unknown as { ws: FakeWS }).ws
    fake.emit({ type: 'snapshot', focus: { symbol: 'AAPL' } })
    expect((got[0] as { focus: { symbol: string } }).focus.symbol).toBe('AAPL')
    client.stop()
  })
})
