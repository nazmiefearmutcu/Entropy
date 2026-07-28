/** Number / string formatting helpers. All output uses tabular figures upstream. */

export const DASH = '–'
export const UP_GLYPH = '▲'
export const DOWN_GLYPH = '▼'
export const FLAT_GLYPH = '–'

export function price(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return DASH
  const abs = Math.abs(v)
  const d = abs !== 0 && abs < 1 ? Math.max(digits, 4) : digits
  return v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
}

export function pct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return `${v >= 0 ? '+' : '-'}${Math.abs(v).toFixed(digits)}%`
}

export function signed(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return `${v >= 0 ? '+' : '-'}${Math.abs(v).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

export function money(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function compact(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return DASH
  const a = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(2)}K`
  return `${sign}${a.toFixed(a < 10 ? 2 : 0)}`
}

/** -1 / 0 / +1 sign used to drive semantic colour and the direction glyph. */
export function dir(v: number | null | undefined): -1 | 0 | 1 {
  if (v == null || !Number.isFinite(v) || v === 0) return 0
  return v > 0 ? 1 : -1
}

export function dirGlyph(v: number | null | undefined): string {
  const d = dir(v)
  return d === 1 ? UP_GLYPH : d === -1 ? DOWN_GLYPH : FLAT_GLYPH
}

/** Tailwind text colour class for a semantic delta. Never the only signal. */
export function dirClass(v: number | null | undefined): string {
  const d = dir(v)
  return d === 1 ? 'text-up' : d === -1 ? 'text-down' : 'text-ink-mute'
}

export function clock(d = new Date()): string {
  return d.toLocaleTimeString('en-GB', { hour12: false })
}

/** "1m" -> 60, "15m" -> 900, "1h" -> 3600, "1d" -> 86400. 0 when unparseable. */
export function timeframeSeconds(tf: string): number {
  const m = /^(\d+)\s*([smhdw])$/i.exec(tf.trim())
  if (!m) return 0
  const n = parseInt(m[1], 10)
  const unit = m[2].toLowerCase()
  const mult: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400, w: 604800 }
  return n * (mult[unit] ?? 0)
}

export function humanSeconds(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return DASH
  if (s < 60) return `${s}s`
  if (s < 3600) return `${(s / 60).toFixed(s % 60 === 0 ? 0 : 1)}m`
  if (s < 86400) return `${(s / 3600).toFixed(s % 3600 === 0 ? 0 : 1)}h`
  return `${(s / 86400).toFixed(s % 86400 === 0 ? 0 : 1)}d`
}

export function truncateMiddle(s: string, max = 46): string {
  if (s.length <= max) return s
  const head = Math.ceil((max - 1) / 2)
  const tail = Math.floor((max - 1) / 2)
  return `${s.slice(0, head)}…${s.slice(s.length - tail)}`
}
