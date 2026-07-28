import { useCallback, useEffect, useRef, useState } from 'react'
import type { SettingsPatch, SettingsPayload } from '../contract'
import { getMeta, getSettings, putSettings } from '../api'
import { FALLBACK_INTERVALS, FALLBACK_TIMEFRAMES } from '../defaults'
import { truncateMiddle } from '../format'
import { usePort } from '../port'
import { setConfig, useConfig, useFocusInterval } from '../store'
import { reportAck } from '../toast'
import type { SettingsTab } from '../ui-state'
import { setSettingsTab } from '../ui-state'
import {
  Button,
  Dot,
  IconButton,
  NumberField,
  SelectField,
  TextField,
  Toggle,
} from '../ui/controls'
import { BotSettings } from './BotSettings'
import { CadenceExplainer, Row2, Row3, Section } from './settings-parts'

const TABS: { id: SettingsTab; label: string }[] = [
  { id: 'general', label: 'General' },
  { id: 'chart', label: 'Chart' },
  { id: 'scanner', label: 'Scanner' },
  { id: 'feeds', label: 'Feeds' },
  { id: 'bot', label: 'Bot' },
]

function mergeDraft(cur: SettingsPayload, patch: SettingsPatch): SettingsPayload {
  const next: SettingsPayload = { app: { ...cur.app }, bot: { ...cur.bot } }
  if (patch.app) Object.assign(next.app, patch.app)
  if (patch.bot) {
    const { consensus, risk_overrides: risk, live, ...rest } = patch.bot
    Object.assign(next.bot, rest)
    if (consensus) next.bot.consensus = { ...cur.bot.consensus, ...consensus }
    if (risk) next.bot.risk_overrides = { ...cur.bot.risk_overrides, ...risk }
    if (live) next.bot.live = { ...cur.bot.live, ...live }
  }
  return next
}

/**
 * Settings surface. Controls write straight through to `PUT /api/settings`;
 * because the sidecar validates all-or-nothing, a rejection keeps the edit on
 * screen and says plainly that nothing was applied.
 */
export function SettingsDrawer({ tab, onClose }: { tab: SettingsTab; onClose: () => void }) {
  const port = usePort()
  const { meta, settings } = useConfig()
  const resolvedInterval = useFocusInterval()
  const [draft, setDraft] = useState<SettingsPayload | null>(settings)
  const [problems, setProblems] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const reload = useCallback(async () => {
    try {
      const fresh = await getSettings(port)
      setConfig({ settings: fresh, error: null })
      setDraft(fresh)
      setProblems([])
      setLoadError(null)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    }
  }, [port])

  useEffect(() => {
    void reload()
    if (!meta) {
      getMeta(port)
        .then((m) => setConfig({ meta: m }))
        .catch(() => undefined)
    }
    // meta is intentionally read once on open
  }, [reload, port, meta])

  const apply = useCallback(
    async (patch: SettingsPatch) => {
      setDraft((d) => (d ? mergeDraft(d, patch) : d))
      setSaving(true)
      const ack = await putSettings(port, patch)
      setSaving(false)
      reportAck(ack)
      if (ack.ok) {
        setProblems([])
        await reload()
      } else {
        setProblems(ack.problems.length > 0 ? ack.problems : [ack.message])
      }
    },
    [port, reload],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const app = draft?.app
  const bot = draft?.bot

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onMouseDown={onClose}>
      <aside
        ref={panelRef}
        role="dialog"
        aria-label="Settings"
        className="drawer-in flex h-full w-[620px] max-w-full flex-col bg-panel shadow-drawer"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-md font-semibold text-ink">Settings</h2>
          {saving && (
            <span className="inline-flex items-center gap-1.5 text-micro uppercase tracking-label text-accent">
              <Dot tone="accent" pulse /> saving
            </span>
          )}
          <span
            className="ml-auto max-w-[280px] truncate font-mono text-micro text-ink-faint"
            title={meta?.settings_path ?? 'settings path unknown'}
          >
            {meta?.settings_path ? truncateMiddle(meta.settings_path, 44) : 'path unknown'}
          </span>
          <IconButton icon="close" label="Close settings" onClick={onClose} />
        </header>

        {problems.length > 0 && (
          <div className="shrink-0 border-b border-down/40 bg-down-soft px-4 py-2">
            <p className="text-xs font-semibold text-down">
              Rejected — nothing was applied. Validation covers app and bot together.
            </p>
            <ul className="mt-1 space-y-0.5">
              {problems.map((p, i) => (
                <li key={i} className="font-mono text-xs text-ink-dim">
                  {p}
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={() => void reload()}
              className="mt-1.5 text-xs text-ink-mute underline decoration-dotted hover:text-ink"
            >
              Discard my edits and reload from disk
            </button>
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <nav
            aria-label="Settings sections"
            className="flex w-[124px] shrink-0 flex-col gap-px border-r border-line bg-sunken py-1"
          >
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                aria-current={tab === t.id ? 'page' : undefined}
                onClick={() => setSettingsTab(t.id)}
                className={`border-l-2 px-3 py-1.5 text-left text-sm transition-colors duration-150 ${
                  tab === t.id
                    ? 'border-accent bg-panel font-semibold text-accent'
                    : 'border-transparent text-ink-mute hover:bg-panel hover:text-ink'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {loadError && (
              <div className="m-4 rounded-[3px] bg-down-soft px-3 py-2">
                <p className="text-sm font-semibold text-down">Could not read settings</p>
                <p className="mt-0.5 font-mono text-xs text-ink-dim">{loadError}</p>
                <Button size="sm" className="mt-2" onClick={() => void reload()}>
                  Retry
                </Button>
              </div>
            )}

            {!app || !bot ? (
              !loadError && <p className="px-4 py-4 text-sm text-ink-mute">loading configuration…</p>
            ) : (
              <>
                {tab === 'general' && (
                  <>
                    <Section title="Appearance" caption="Theme is applied by the sidecar renderer.">
                      <SelectField
                        label="Theme"
                        value={app.theme}
                        options={(meta?.themes ?? [app.theme]).map((t) => ({ value: t, label: t }))}
                        onChange={(v) => void apply({ app: { theme: v } })}
                      />
                    </Section>
                    <Section
                      title="Files"
                      caption="Where this session writes its log, trade tape and followed symbols."
                    >
                      <TextField
                        label="Console log"
                        value={app.console_log_path}
                        onCommit={(v) => void apply({ app: { console_log_path: v } })}
                      />
                      <TextField
                        label="Trade CSV"
                        value={app.trade_csv_path}
                        onCommit={(v) => void apply({ app: { trade_csv_path: v } })}
                      />
                      <TextField
                        label="Watchlist file"
                        value={app.watchlist_path}
                        onCommit={(v) => void apply({ app: { watchlist_path: v } })}
                      />
                    </Section>
                    <Section
                      title="Determinism"
                      caption="Seeds the simulator so a session can be replayed."
                    >
                      <NumberField
                        label="Seed"
                        value={app.seed}
                        step={1}
                        onCommit={(v) => void apply({ app: { seed: v } })}
                      />
                    </Section>
                    <Section title="Persistence">
                      <p className="font-mono text-xs leading-relaxed text-ink-mute">
                        {meta?.settings_path ?? 'settings path unavailable'}
                      </p>
                    </Section>
                  </>
                )}

                {tab === 'chart' && (
                  <>
                    <Section
                      title="Candles"
                      caption="Candle width is a chart concern only. It does not move the scanner."
                    >
                      <Row2>
                        <SelectField
                          label="Chart type"
                          value={app.chart_type}
                          options={[
                            { value: 'candles', label: 'candles' },
                            { value: 'line', label: 'line' },
                          ]}
                          onChange={(v) => void apply({ app: { chart_type: v } })}
                        />
                        <SelectField
                          label="Candle interval"
                          value={app.chart_interval}
                          options={[
                            { value: '', label: `follow scanner (${resolvedInterval || '–'})` },
                            ...(meta?.chart_intervals ?? FALLBACK_INTERVALS).map((i) => ({
                              value: i,
                              label: i,
                            })),
                          ]}
                          onChange={(v) => void apply({ app: { chart_interval: v } })}
                          hint='Empty means "follow the scanner timeframe".'
                        />
                      </Row2>
                      <NumberField
                        label="Bars kept"
                        value={app.chart_bars}
                        step={50}
                        min={10}
                        onCommit={(v) => void apply({ app: { chart_bars: v } })}
                      />
                    </Section>
                    <Section title="Overlays">
                      <div className="flex items-center justify-between py-1.5">
                        <div>
                          <p className="text-sm text-ink">Volume pane</p>
                          <p className="text-xs text-ink-mute">Histogram under the price series.</p>
                        </div>
                        <Toggle
                          label="Show volume"
                          checked={app.show_volume}
                          onChange={(v) => void apply({ app: { show_volume: v } })}
                        />
                      </div>
                      <div className="flex items-center justify-between py-1.5">
                        <div>
                          <p className="text-sm text-ink">Depth ladder</p>
                          <p className="text-xs text-ink-mute">
                            Needs a live equity source; in sim mode the book stays empty.
                          </p>
                        </div>
                        <Toggle
                          label="Show depth"
                          checked={app.show_depth}
                          onChange={(v) => void apply({ app: { show_depth: v } })}
                        />
                      </div>
                      <Row2>
                        <NumberField
                          label="Depth bins"
                          value={app.depth_bins}
                          step={1}
                          min={1}
                          onCommit={(v) => void apply({ app: { depth_bins: v } })}
                        />
                        <NumberField
                          label="Depth rows"
                          value={app.depth_top_n}
                          step={1}
                          min={1}
                          onCommit={(v) => void apply({ app: { depth_top_n: v } })}
                        />
                      </Row2>
                    </Section>
                  </>
                )}

                {tab === 'scanner' && (
                  <Section
                    title="Scanner timeframe"
                    caption="The rolling window behind new highs, new lows, momentum and breadth."
                  >
                    <SelectField
                      label="Timeframe"
                      value={app.timeframe}
                      options={(meta?.timeframes ?? FALLBACK_TIMEFRAMES).map((t) => ({
                        value: t,
                        label: t,
                      }))}
                      onChange={(v) => void apply({ app: { timeframe: v } })}
                    />
                    <div className="mt-3">
                      <CadenceExplainer
                        scannerTf={app.timeframe}
                        chartInterval={app.chart_interval}
                        resolvedInterval={resolvedInterval}
                        botTf={bot.timeframe}
                        botBarS={bot.bar_s}
                      />
                    </div>
                  </Section>
                )}

                {tab === 'feeds' && (
                  <>
                    <Section title="Equities">
                      <div className="flex items-center justify-between py-1.5">
                        <div>
                          <p className="text-sm text-ink">Enable equities</p>
                          <p className="text-xs text-ink-mute">Turns the equity feed on or off.</p>
                        </div>
                        <Toggle
                          label="Enable equities"
                          checked={app.enable_equities}
                          onChange={(v) => void apply({ app: { enable_equities: v } })}
                        />
                      </div>
                      <Row2>
                        <SelectField
                          label="Equity source"
                          value={app.equity_source}
                          options={(meta?.equity_sources ?? [app.equity_source]).map((s) => ({
                            value: s,
                            label: s,
                          }))}
                          onChange={(v) => void apply({ app: { equity_source: v } })}
                          disabled={!app.enable_equities}
                        />
                        <NumberField
                          label="Equity ticks/s"
                          value={app.equity_tps}
                          step={1}
                          min={0}
                          suffix="tps"
                          onCommit={(v) => void apply({ app: { equity_tps: v } })}
                          disabled={!app.enable_equities}
                        />
                      </Row2>
                      <TextField
                        label="Equity strategy symbol"
                        value={app.strategy_symbol}
                        onCommit={(v) => void apply({ app: { strategy_symbol: v } })}
                      />
                    </Section>
                    <Section title="Crypto">
                      <div className="flex items-center justify-between py-1.5">
                        <div>
                          <p className="text-sm text-ink">Enable crypto</p>
                          <p className="text-xs text-ink-mute">24/7 venues, no session clock.</p>
                        </div>
                        <Toggle
                          label="Enable crypto"
                          checked={app.enable_crypto}
                          onChange={(v) => void apply({ app: { enable_crypto: v } })}
                        />
                      </div>
                      <TextField
                        label="Crypto strategy symbol"
                        value={app.crypto_strategy_symbol}
                        onCommit={(v) => void apply({ app: { crypto_strategy_symbol: v } })}
                      />
                    </Section>
                    <Section title="Risk profile" caption="Applied to the scanner's own sizing hints.">
                      <Row3>
                        <SelectField
                          label="Profile"
                          value={app.risk_profile}
                          options={(meta?.risk_profiles ?? []).map((p) => ({
                            value: p.name,
                            label: p.name,
                          }))}
                          onChange={(v) => void apply({ app: { risk_profile: v } })}
                        />
                      </Row3>
                    </Section>
                  </>
                )}

                {tab === 'bot' && (
                  <BotSettings
                    bot={bot}
                    meta={meta}
                    scannerTf={app.timeframe}
                    chartInterval={app.chart_interval}
                    resolvedInterval={resolvedInterval}
                    apply={(patch) => void apply(patch)}
                  />
                )}
              </>
            )}
          </div>
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-line bg-raised px-4 py-1.5">
          <p className="text-micro text-ink-mute">
            Changes are written immediately and validated as one unit.
          </p>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void reload()}>
            Reload
          </Button>
        </footer>
      </aside>
    </div>
  )
}
