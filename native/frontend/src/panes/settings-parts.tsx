import type { ReactNode } from 'react'
import { humanSeconds, timeframeSeconds } from '../format'

export function Section({
  title,
  caption,
  children,
}: {
  title: string
  caption?: string
  children: ReactNode
}) {
  return (
    <section className="border-b border-line px-4 py-3 last:border-0">
      <h3 className="text-xs font-bold uppercase tracking-label text-ink">{title}</h3>
      {caption && <p className="mt-0.5 max-w-[58ch] text-xs leading-relaxed text-ink-mute">{caption}</p>}
      <div className="mt-1.5">{children}</div>
    </section>
  )
}

export function Row2({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-x-4">{children}</div>
}

export function Row3({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-3 gap-x-3">{children}</div>
}

/**
 * The three cadences, side by side. Conflating them was the bug the user hit;
 * this block exists so the distinction is impossible to miss.
 */
export function CadenceExplainer({
  scannerTf,
  chartInterval,
  resolvedInterval,
  botTf,
  botBarS,
}: {
  scannerTf: string
  chartInterval: string
  resolvedInterval: string
  botTf: string
  botBarS: number
}) {
  const botSeconds = botBarS > 0 ? botBarS : timeframeSeconds(botTf)
  const items = [
    {
      key: 'scanner',
      label: 'Scanner',
      value: scannerTf || 'auto',
      note: 'rolling highs / lows, momentum, breadth',
      tone: 'text-accent',
    },
    {
      key: 'chart',
      label: 'Chart candles',
      value: chartInterval === '' ? `follow (${resolvedInterval || '–'})` : chartInterval,
      note: 'candle width only — independent of the scanner',
      tone: 'text-info',
    },
    {
      key: 'bot',
      label: 'Bot',
      value: botBarS > 0 ? `${humanSeconds(botBarS)} override` : botTf || '–',
      note:
        botSeconds > 0
          ? `decides every ${humanSeconds(botSeconds)}`
          : 'the bot computes on its own clock',
      tone: 'text-up',
    },
  ]
  return (
    <div className="grid grid-cols-3 gap-px overflow-hidden rounded-[3px] bg-line">
      {items.map((i) => (
        <div key={i.key} className="bg-sunken px-2.5 py-2">
          <div className="text-micro uppercase tracking-label text-ink-mute">{i.label}</div>
          <div className={`font-mono tnum text-sm font-semibold ${i.tone}`}>{i.value}</div>
          <p className="mt-0.5 text-micro leading-snug text-ink-faint">{i.note}</p>
        </div>
      ))}
    </div>
  )
}
