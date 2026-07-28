import { useState } from 'react'
import { putSettings } from '../api'
import { FALLBACK_INTERVALS, FALLBACK_TIMEFRAMES, FOLLOW } from '../defaults'
import { usePort } from '../port'
import { useConfig, useFocusInterval, useSettingsView } from '../store'
import { reportAck } from '../toast'
import { Segmented } from '../ui/controls'
import { Icon } from '../ui/Icon'

const CANDLE_HELP =
  'Chart candle width. Independent of the scanner timeframe — "Follow" makes it track the scanner instead.'
const SCANNER_HELP =
  'Scanner cadence: the rolling window for new highs/lows, momentum and breadth. It does not change the chart candles.'

/** Presentational segmented control for the chart candle interval. */
export function ChartIntervalControl({
  intervals,
  value,
  resolved,
  timeframe,
  pending,
  onChange,
}: {
  intervals: string[]
  value: string
  /** Candle width actually in force — equals `timeframe` only while following. */
  resolved: string
  /** The scanner timeframe. Named separately because the Follow option promises
   *  to track THAT, not whatever interval happens to be selected right now. */
  timeframe: string
  pending?: boolean
  onChange: (v: string) => void
}) {
  const options = [
    {
      value: FOLLOW,
      label: 'Follow',
      title: `Follow the scanner timeframe (now ${timeframe || '–'})`,
    },
    ...intervals.map((i) => ({ value: i, label: i, title: `${i} candles` })),
  ]
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span
        title={CANDLE_HELP}
        className="inline-flex shrink-0 cursor-help items-center gap-1 text-micro font-semibold uppercase tracking-label text-ink-mute"
      >
        Candles
        <Icon name="link" size={10} className="text-ink-faint" />
      </span>
      <div className="min-w-0 overflow-x-auto">
        <Segmented
          label="Chart candle interval"
          options={options}
          value={value}
          onChange={onChange}
          pending={pending}
        />
      </div>
      {value === FOLLOW && (
        <span
          className="shrink-0 font-mono text-micro text-ink-faint"
          title="Resolved candle width while following the scanner"
        >
          = {resolved || '–'}
        </span>
      )}
    </div>
  )
}

/** Presentational dropdown for the scanner timeframe. */
export function ScannerTimeframeControl({
  timeframes,
  value,
  pending,
  onChange,
}: {
  timeframes: string[]
  value: string
  pending?: boolean
  onChange: (v: string) => void
}) {
  const known = timeframes.includes(value)
  return (
    <label className="flex shrink-0 items-center gap-2" title={SCANNER_HELP}>
      <span className="cursor-help text-micro font-semibold uppercase tracking-label text-ink-mute">
        Scanner
      </span>
      <span className="relative">
        <select
          aria-label="Scanner timeframe"
          value={value}
          disabled={pending}
          onChange={(e) => onChange(e.target.value)}
          className="h-[22px] appearance-none rounded-[3px] bg-sunken pl-2 pr-6 font-mono tnum text-xs text-accent hover:bg-raised focus:outline-none disabled:opacity-60"
        >
          {!known && <option value={value}>{value || 'auto'}</option>}
          {timeframes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 text-ink-mute">
          <Icon name="chevron-down" size={10} />
        </span>
      </span>
    </label>
  )
}

/**
 * Wires both cadence controls to `PUT /api/settings`.
 *
 * The active value always comes from the settings echoed on the last snapshot,
 * never from local state — a rejected write leaves the control where the sidecar
 * says it is and raises the returned `problems`.
 */
export function CadenceControls() {
  const port = usePort()
  const { meta } = useConfig()
  const settings = useSettingsView()
  const resolvedInterval = useFocusInterval()
  const [pendingInterval, setPendingInterval] = useState(false)
  const [pendingTimeframe, setPendingTimeframe] = useState(false)

  const applyInterval = async (v: string) => {
    setPendingInterval(true)
    const ack = await putSettings(port, { app: { chart_interval: v } })
    setPendingInterval(false)
    reportAck(ack)
  }

  const applyTimeframe = async (v: string) => {
    setPendingTimeframe(true)
    const ack = await putSettings(port, { app: { timeframe: v } })
    setPendingTimeframe(false)
    reportAck(ack)
  }

  return (
    <div className="flex min-w-0 items-center gap-4">
      <ChartIntervalControl
        intervals={meta?.chart_intervals ?? FALLBACK_INTERVALS}
        value={settings.chart_interval}
        resolved={resolvedInterval}
        timeframe={settings.timeframe}
        pending={pendingInterval}
        onChange={(v) => void applyInterval(v)}
      />
      <span className="h-4 w-px shrink-0 bg-line" />
      <ScannerTimeframeControl
        timeframes={meta?.timeframes ?? FALLBACK_TIMEFRAMES}
        value={settings.timeframe}
        pending={pendingTimeframe}
        onChange={(v) => void applyTimeframe(v)}
      />
    </div>
  )
}
