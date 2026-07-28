import type { SnapshotMessage } from './contract'

export interface StreamHandlers {
  onSnapshot: (m: SnapshotMessage) => void
  onStatus?: (connected: boolean) => void
  /** Fired when a frame cannot be parsed; the socket itself stays up. */
  onError?: (message: string) => void
}
type WSFactory = (url: string) => WebSocket

export class StreamClient {
  private ws: WebSocket | null = null
  private backoff = 500
  private stopped = false
  private port: number
  private handlers: StreamHandlers
  private factory: WSFactory
  private timer: ReturnType<typeof setTimeout> | null = null
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
    ws.onmessage = (e) => {
      try {
        this.handlers.onSnapshot(JSON.parse((e as MessageEvent).data))
      } catch (err) {
        this.handlers.onError?.(err instanceof Error ? err.message : String(err))
      }
    }
    ws.onclose = () => {
      this.handlers.onStatus?.(false)
      if (!this.stopped) {
        this.timer = setTimeout(() => this.connect(), this.backoff)
        this.backoff = Math.min(this.backoff * 2, 8000)
      }
    }
  }

  /** Seconds until the next reconnect attempt, for honest status reporting. */
  get retryDelayMs(): number {
    return this.backoff
  }

  stop() {
    this.stopped = true
    if (this.timer != null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    this.ws?.close()
  }
}
