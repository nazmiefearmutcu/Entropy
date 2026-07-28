import { useState } from 'react'
import { putSettings } from '../api'
import { price as fmtPrice, timeframeSeconds, DASH } from '../format'
import { usePort } from '../port'
import { useCandles, useConfig, useFocusInterval, useFocusMeta, useFocusSymbol, useSettingsView } from '../store'
import { reportAck } from '../toast'
import { openPicker, openSettings } from '../ui-state'
import { Chip, IconButton, Segmented, Toggle } from '../ui/controls'
import { Delta, Flash } from '../ui/data'
import { Icon } from '../ui/Icon'
import { FocusChart } from './FocusChart'
import { CadenceControls } from './IntervalControls'

const CHART_TYPES = [
  { value: 'candles', label: 'Candles' },
  { value: 'line', label: 'Line' },
]

/** Symbol identity, hero price, and every control that shapes the chart. */
function ChartHeader() {
  const focus = useFocusMeta()
  const equity = focus.asset === 'EQUITY'
  return (
    <div className="flex shrink-0 items-end gap-4 bg-panel px-3 pb-2 pt-2.5">
      <button
        type="button"
        onClick={() => openPicker('focus')}
        title="Change symbol (Cmd+K)"
        className="group flex items-center gap-2 text-left"
      >
        <span className="font-mono text-xl font-bold tracking-tight text-ink group-hover:text-accent">
          {focus.symbol || 'no symbol'}
        </span>
        <Chip tone={equity ? 'info' : 'accent'}>{focus.asset}</Chip>
        <Icon
          name="search"
          size={12}
          className="text-ink-faint transition-colors duration-150 group-hover:text-accent"
        />
      </button>

      <div className="flex items-baseline gap-3">
        <Flash value={focus.last} className="px-1">
          <span className="font-mono tnum text-hero font-semibold text-ink">
            {fmtPrice(focus.last)}
          </span>
        </Flash>
        <Delta value={focus.pct} size="md" />
      </div>

      <div className="ml-auto text-right">
        <div className="text-micro uppercase tracking-label text-ink-mute">Bars</div>
        <div
          className="font-mono tnum text-sm text-ink-dim"
          title="Resolved candle width for this chart"
        >
          {focus.interval || DASH}
          <span className="ml-1 text-micro text-ink-faint">candles</span>
        </div>
      </div>
    </div>
  )
}

function ChartToolbar() {
  const port = usePort()
  const settings = useSettingsView()
  const [pending, setPending] = useState(false)

  const apply = async (patch: { chart_type?: string; show_volume?: boolean }) => {
    setPending(true)
    const ack = await putSettings(port, { app: patch })
    setPending(false)
    reportAck(ack)
  }

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-4 bg-panel px-3 pb-2">
      <CadenceControls />

      <span className="h-4 w-px bg-line" />

      <div className="flex items-center gap-2">
        <span className="text-micro font-semibold uppercase tracking-label text-ink-mute">Type</span>
        <Segmented
          label="Chart type"
          size="sm"
          options={CHART_TYPES}
          value={settings.chart_type}
          pending={pending}
          onChange={(v) => void apply({ chart_type: v })}
        />
      </div>

      <label className="flex items-center gap-2" title="Show a volume pane under the price series">
        <span className="text-micro font-semibold uppercase tracking-label text-ink-mute">
          Volume
        </span>
        <Toggle
          label="Show volume pane"
          checked={settings.show_volume}
          disabled={pending}
          onChange={(v) => void apply({ show_volume: v })}
        />
      </label>

      <IconButton
        icon="gear"
        label="Chart settings"
        size={20}
        className="ml-auto"
        onClick={() => openSettings('chart')}
      />
    </div>
  )
}

export function ChartPanel() {
  const candles = useCandles()
  // Deliberately narrow: only the hero header cares about the live price, so the
  // toolbar and the chart host do not re-render at 10 Hz.
  const symbol = useFocusSymbol()
  const interval = useFocusInterval()
  const settings = useSettingsView()
  const { settings: cfg } = useConfig()

  const emaFast = cfg?.bot.consensus.ema_fast ?? cfg?.bot.ema_fast ?? 9
  const emaSlow = cfg?.bot.consensus.ema_slow ?? cfg?.bot.ema_slow ?? 21

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-px bg-base">
      <div className="shrink-0">
        <ChartHeader />
        <ChartToolbar />
      </div>
      <FocusChart
        candles={candles}
        symbol={symbol}
        chartType={settings.chart_type}
        showVolume={settings.show_volume}
        emaFast={emaFast}
        emaSlow={emaSlow}
        intervalSeconds={timeframeSeconds(interval)}
      />
    </section>
  )
}
