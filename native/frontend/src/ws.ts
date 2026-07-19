import type { SnapshotMessage } from './contract'

export interface StreamHandlers {
  onSnapshot: (m: SnapshotMessage) => void
  onStatus?: (connected: boolean) => void
}
type WSFactory = (url: string) => WebSocket

export class StreamClient {
  private ws: WebSocket | null = null
  private backoff = 500
  private stopped = false
  private port: number
  private handlers: StreamHandlers
  private factory: WSFactory
  constructor(port: number, handlers: StreamHandlers, factory: WSFactory = (u) => new WebSocket(u)) {
    this.port = port
    this.handlers = handlers
    this.factory = factory
  }

  connect() {
    this.stopped = false
    const ws = this.factory(`ws://127.0.0.1:${this.port}/ws/live`)
    this.ws = ws
    ws.onopen = () => {
      this.backoff = 500
      this.handlers.onStatus?.(true)
    }
    ws.onmessage = (e) => this.handlers.onSnapshot(JSON.parse((e as MessageEvent).data))
    ws.onclose = () => {
      this.handlers.onStatus?.(false)
      if (!this.stopped) {
        setTimeout(() => this.connect(), this.backoff)
        this.backoff = Math.min(this.backoff * 2, 8000)
      }
    }
  }

  stop() {
    this.stopped = true
    this.ws?.close()
  }
}

export async function postCommand(port: number, verb: string, arg = '') {
  await fetch(`http://127.0.0.1:${port}/api/command`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ verb, arg }),
  })
}
