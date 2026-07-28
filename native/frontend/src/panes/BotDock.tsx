import { useEffect, useRef, useState } from 'react'
import type { BotAction, BotPosition, BotStrategy, BotView } from '../contract'
import { botAction } from '../api'
import { humanSeconds, money, price as fmtPrice, signed, timeframeSeconds, DASH } from '../format'
import { usePort } from '../port'
import { useBot, useBotSummary } from '../store'
import { reportAck } from '../toast'
import { openSettings, setBotOpen, toggleBot, useUi } from '../ui-state'
import { Button, Chip, Dot, IconButton } from '../ui/controls'
import { Delta, Stat } from '../ui/data'
import { EmptyState } from '../ui/layout'

type Tab = 'positions' | 'strategies' | 'signals' | 'rejects'

const REGIME_TONE: Record<string, string> = {
  trend: 'text-info',
  range: 'text-accent',
  chop: 'text-ink-mute',
}

function stateFrom(present: boolean, running: boolean, paused: boolean, halted: boolean) {
  if (!present) return { label: 'not started', tone: 'mute' as const, cls: 'text-ink-mute' }
  if (halted) return { label: 'halted', tone: 'down' as const, cls: 'text-down' }
  if (paused) return { label: 'paused', tone: 'accent' as const, cls: 'text-accent' }
  if (running) return { label: 'running', tone: 'up' as const, cls: 'text-up' }
  return { label: 'stopped', tone: 'mute' as const, cls: 'text-ink-mute' }
}

function stateOf(bot: BotView | null) {
  return stateFrom(bot != null, bot?.running ?? false, bot?.paused ?? false, bot?.halted ?? false)
}

/* ------------------------------------------------------------- controls */

function HaltButton({ onHalt, disabled }: { onHalt: () => void; disabled?: boolean }) {
  const [armed, setArmed] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  const arm = () => {
    setArmed(true)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setArmed(false), 4000)
  }

  return (
    <button
      type="button"
      disabled={disabled}
      title={
        armed
          ? 'Click again to halt: flattens intent and blocks new orders'
          : 'Emergency halt — requires confirmation'
      }
      onClick={() => {
        if (armed) {
          setArmed(false)
          if (timer.current) clearTimeout(timer.current)
          onHalt()
        } else {
          arm()
        }
      }}
      className={`h-7 w-full rounded-[3px] text-xs font-bold uppercase tracking-label transition-colors duration-150 disabled:opacity-35 ${
        armed
          ? 'halt-armed bg-down text-white'
          : 'bg-down-soft text-down ring-1 ring-inset ring-down/40 hover:bg-down hover:text-white'
      }`}
    >
      {armed ? 'Confirm halt' : 'Emergency halt'}
    </button>
  )
}

function ControlColumn({ bot, onAction }: { bot: BotView | null; onAction: (a: BotAction) => void }) {
  const st = stateOf(bot)
  const running = bot?.running ?? false
  const paused = bot?.paused ?? false
  const halted = bot?.halted ?? false
  const live = (bot?.mode ?? '').toLowerCase() === 'live'
  const barSeconds = bot ? (bot.bar_s > 0 ? bot.bar_s : timeframeSeconds(bot.timeframe)) : 0

  return (
    <div className="flex w-[214px] shrink-0 flex-col gap-2 bg-panel px-2.5 py-2">
      <div className="flex items-center gap-2">
        <Dot tone={st.tone} pulse={running && !paused} />
        <span className={`text-sm font-semibold uppercase tracking-label ${st.cls}`}>
          {st.label}
        </span>
        <span className="ml-auto">
          {live ? <Chip tone="loud">live money</Chip> : <Chip tone="neutral">{bot?.mode || 'paper'}</Chip>}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-1">
        {running ? (
          <Button variant="down" size="sm" onClick={() => onAction('stop')}>
            Stop
          </Button>
        ) : (
          <Button variant="up" size="sm" onClick={() => onAction('start')} disabled={halted}>
            Start
          </Button>
        )}
        {paused ? (
          <Button size="sm" onClick={() => onAction('resume')} disabled={!running}>
            Resume
          </Button>
        ) : (
          <Button size="sm" onClick={() => onAction('pause')} disabled={!running}>
            Pause
          </Button>
        )}
      </div>

      <HaltButton onHalt={() => onAction('halt')} disabled={!bot} />

      <div className="mt-auto grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-line pt-2">
        <Stat
          label="Bot timeframe"
          value={bot?.timeframe || DASH}
          title="The bot's own computation cadence — separate from both the scanner timeframe and the chart candles"
        />
        <Stat
          label="Bar width"
          value={barSeconds > 0 ? humanSeconds(barSeconds) : DASH}
          tone="text-ink-dim"
          title={bot && bot.bar_s > 0 ? `bar_s override: ${bot.bar_s}s` : 'following bot timeframe'}
        />
        <Stat
          label="Risk profile"
          value={bot?.risk_profile || DASH}
          tone="text-accent"
          title={bot?.risk_description}
        />
        <Stat
          label="Ticks"
          value={bot ? bot.ticks.toLocaleString('en-US') : DASH}
          tone="text-ink-dim"
        />
      </div>

      <div className="flex items-center gap-2">
        {bot && !bot.warm && (
          <span
            className="inline-flex items-center gap-1 text-micro uppercase tracking-label text-accent"
            title="Indicators are still filling their lookback windows; no signal is emitted until warm"
          >
            <Dot tone="accent" pulse /> warming up
          </span>
        )}
        {bot?.warm && (
          <span className="inline-flex items-center gap-1 text-micro uppercase tracking-label text-ink-mute">
            <Dot tone="up" /> warm
          </span>
        )}
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={() => openSettings('bot')}
          title="Bot settings: mode, risk, strategies, cadence and signal logic"
        >
          Settings
        </Button>
      </div>
    </div>
  )
}

/* ----------------------------------------------------------------- pnl */

function PnlRow({ bot }: { bot: BotView }) {
  return (
    <div className="flex shrink-0 items-center gap-6 bg-panel px-3 py-2">
      <Stat label="Equity" value={money(bot.equity)} size="md" />
      <Stat label="Cash" value={money(bot.cash)} tone="text-ink-dim" />
      <span className="h-7 w-px bg-line" />
      <div>
        <div className="text-micro uppercase tracking-label text-ink-mute">Realised</div>
        <Delta value={bot.realized_pnl} kind="abs" />
      </div>
      <div>
        <div className="text-micro uppercase tracking-label text-ink-mute">Unrealised</div>
        <Delta value={bot.unrealized_pnl} kind="abs" />
      </div>
      <div>
        <div className="text-micro uppercase tracking-label text-ink-mute">Day</div>
        <Delta value={bot.daily_pnl} kind="abs" size="md" />
      </div>
      <span className="h-7 w-px bg-line" />
      <Stat label="Open" value={String(bot.open_count)} tone="text-ink-dim" />
    </div>
  )
}

/* -------------------------------------------------------------- tables */

const TH = 'px-2 py-1 text-micro font-semibold uppercase tracking-label text-ink-mute'
const TD = 'px-2 py-[3px] font-mono tnum text-xs'

function Positions({ rows }: { rows: BotPosition[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No open positions"
        hint="Filled entries appear here with their live mark, stop and target."
      />
    )
  }
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-full border-collapse">
        <thead className="sticky top-0 bg-raised">
          <tr>
            <th className={`${TH} text-left`}>Symbol</th>
            <th className={`${TH} text-left`}>Side</th>
            <th className={`${TH} text-right`}>Qty</th>
            <th className={`${TH} text-right`}>Entry</th>
            <th className={`${TH} text-right`}>Mark</th>
            <th className={`${TH} text-right`}>Unrealised</th>
            <th className={`${TH} text-right`}>Stop</th>
            <th className={`${TH} text-right`}>Target</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const long = p.side.toLowerCase() !== 'short'
            return (
              <tr key={`${p.symbol}:${p.side}`} className="odd:bg-sunken/40 hover:bg-hover">
                <td className={`${TD} text-left font-semibold text-ink`}>{p.symbol}</td>
                <td className={`${TD} text-left`}>
                  <span className={long ? 'text-up' : 'text-down'}>
                    {long ? 'LONG' : 'SHORT'}
                  </span>
                </td>
                <td className={`${TD} text-right text-ink-dim`}>{money(p.qty, 4)}</td>
                <td className={`${TD} text-right text-ink-dim`}>{fmtPrice(p.entry_px)}</td>
                <td className={`${TD} text-right text-ink`}>{fmtPrice(p.mark_px)}</td>
                <td
                  className={`${TD} text-right ${
                    p.unrealized_pnl >= 0 ? 'text-up' : 'text-down'
                  }`}
                >
                  {signed(p.unrealized_pnl)}
                </td>
                <td className={`${TD} text-right text-down`}>{fmtPrice(p.stop_px)}</td>
                <td className={`${TD} text-right text-up`}>{fmtPrice(p.tp_px)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Strategies({ rows }: { rows: BotStrategy[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No strategies enabled"
        hint="Pick at least one strategy in Bot settings, otherwise the consensus has nothing to vote on."
      />
    )
  }
  return (
    <div className="min-h-0 flex-1 overflow-auto px-2 py-1.5">
      {rows.map((s) => {
        const symbols = Object.keys({ ...s.regimes, ...s.directions }).sort()
        return (
          <div key={s.name} className="border-b border-line py-1.5 last:border-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-ink">{s.name}</span>
              {s.warm ? (
                <Chip tone="up">warm</Chip>
              ) : (
                <Chip tone="accent" title="Still filling indicator lookbacks">
                  warming
                </Chip>
              )}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {symbols.length === 0 && <span className="text-xs text-ink-faint">no symbols yet</span>}
              {symbols.map((sym) => {
                const regime = s.regimes[sym] ?? 'unknown'
                const d = s.directions[sym] ?? 0
                return (
                  <span key={sym} className="inline-flex items-baseline gap-1.5 font-mono text-xs">
                    <span className="text-ink-dim">{sym}</span>
                    <span className={REGIME_TONE[regime] ?? 'text-ink-faint'}>{regime}</span>
                    <span
                      className={
                        d > 0 ? 'text-up' : d < 0 ? 'text-down' : 'text-ink-faint'
                      }
                      title="Directional vote"
                    >
                      {d > 0 ? '+1' : d < 0 ? '-1' : '0'}
                    </span>
                  </span>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function LogList({ lines, kind }: { lines: string[]; kind: 'signals' | 'rejects' }) {
  if (lines.length === 0) {
    return (
      <EmptyState
        title={kind === 'signals' ? 'No signals yet' : 'No rejects yet'}
        hint={
          kind === 'signals'
            ? 'Every accepted consensus decision is logged here as it happens.'
            : 'When the bot declines to trade, the reason lands here — risk gate, cooldown, participation or warmup.'
        }
      />
    )
  }
  return (
    <div className="min-h-0 flex-1 overflow-auto px-2 py-1">
      {lines.map((l, i) => (
        <div
          key={`${i}:${l}`}
          className={`border-l-2 py-[2px] pl-2 font-mono text-xs ${
            kind === 'signals' ? 'border-up/50 text-ink-dim' : 'border-down/50 text-ink-mute'
          }`}
        >
          {l}
        </div>
      ))}
    </div>
  )
}

/* ---------------------------------------------------------------- dock */

function CollapsedStrip({ onExpand }: { onExpand: () => void }) {
  const bot = useBotSummary()
  const st = stateFrom(bot.present, bot.running, bot.paused, bot.halted)
  return (
    <div className="flex h-9 shrink-0 items-center gap-3 bg-panel px-3">
      <span className="text-micro font-bold uppercase tracking-mark text-ink-dim">Bot</span>
      <Dot tone={st.tone} pulse={bot.running && !bot.paused} />
      <span className={`text-xs font-semibold uppercase ${st.cls}`}>{st.label}</span>
      {bot.present && (
        <>
          {bot.mode.toLowerCase() === 'live' ? (
            <Chip tone="loud">live money</Chip>
          ) : (
            <Chip tone="neutral">{bot.mode}</Chip>
          )}
          <span className="ml-4 font-mono tnum text-xs text-ink-dim">
            <span className="mr-1 text-micro uppercase tracking-label text-ink-mute">equity</span>
            {money(bot.equity)}
          </span>
          <span className="ml-4 inline-flex items-baseline gap-1.5">
            <span className="text-micro uppercase tracking-label text-ink-mute">day</span>
            <Delta value={bot.dailyPnl} kind="abs" />
          </span>
          <span className="ml-4 font-mono tnum text-xs text-ink-dim">
            <span className="mr-1 text-micro uppercase tracking-label text-ink-mute">open</span>
            {bot.openCount}
          </span>
        </>
      )}
      <button
        type="button"
        onClick={onExpand}
        className="ml-auto inline-flex items-center gap-1 text-micro uppercase tracking-label text-ink-mute hover:text-accent"
      >
        Expand
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
          <path d="M12.4 10.1 8 5.7l-4.4 4.4 1.1 1.1L8 7.9l3.3 3.3Z" />
        </svg>
      </button>
    </div>
  )
}

export function BotDock() {
  const ui = useUi()
  const bot = useBot()
  const port = usePort()
  const [tab, setTab] = useState<Tab>('positions')
  const [busy, setBusy] = useState(false)

  const act = async (a: BotAction) => {
    setBusy(true)
    const ack = await botAction(port, a)
    setBusy(false)
    reportAck(ack, ack.ok ? `Bot ${a}` : undefined)
  }

  if (!ui.botOpen) return <CollapsedStrip onExpand={() => setBotOpen(true)} />

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: 'positions', label: 'Positions', count: bot?.positions.length ?? 0 },
    { id: 'strategies', label: 'Strategies', count: bot?.strategies.length ?? 0 },
    { id: 'signals', label: 'Signals', count: bot?.last_signals.length ?? 0 },
    { id: 'rejects', label: 'Rejects', count: bot?.last_rejects.length ?? 0 },
  ]

  return (
    <section
      className="flex shrink-0 gap-px bg-base"
      style={{ height: ui.botHeight }}
      aria-label="Trading bot"
    >
      <div className={busy ? 'pointer-events-none opacity-70' : ''}>
        <ControlColumn bot={bot} onAction={(a) => void act(a)} />
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-px bg-base">
        {bot ? (
          <>
            <PnlRow bot={bot} />
            <div className="flex min-h-0 flex-1 flex-col bg-panel">
              <div className="flex h-[26px] shrink-0 items-center gap-px bg-raised px-1">
                {tabs.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setTab(t.id)}
                    aria-pressed={tab === t.id}
                    className={`inline-flex h-[22px] items-center gap-1.5 rounded-[2px] px-2.5 text-micro font-semibold uppercase tracking-label transition-colors duration-150 ${
                      tab === t.id
                        ? 'bg-panel text-accent'
                        : 'text-ink-mute hover:bg-panel hover:text-ink-dim'
                    }`}
                  >
                    {t.label}
                    {t.count != null && (
                      <span className="font-mono tnum text-ink-faint">{t.count}</span>
                    )}
                  </button>
                ))}
                <IconButton
                  icon="chevron-down"
                  label="Collapse bot panel (Cmd+B)"
                  size={20}
                  className="ml-auto"
                  onClick={() => toggleBot()}
                />
              </div>
              {tab === 'positions' && <Positions rows={bot.positions} />}
              {tab === 'strategies' && <Strategies rows={bot.strategies} />}
              {tab === 'signals' && <LogList lines={bot.last_signals} kind="signals" />}
              {tab === 'rejects' && <LogList lines={bot.last_rejects} kind="rejects" />}
            </div>
          </>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col bg-panel">
            <div className="flex h-[26px] shrink-0 items-center bg-raised px-2">
              <span className="text-micro font-semibold uppercase tracking-label text-ink-mute">
                Bot engine
              </span>
              <IconButton
                icon="chevron-down"
                label="Collapse bot panel (Cmd+B)"
                size={20}
                className="ml-auto"
                onClick={() => toggleBot()}
              />
            </div>
            <EmptyState
              title="Bot not started"
              hint="The sidecar creates a bot session on the first start. Review mode, risk profile and strategies first — then start it; P&L, positions, per-strategy regimes and the signal/reject logs appear here."
              action={
                <div className="flex gap-2">
                  <Button variant="up" onClick={() => void act('start')}>
                    Start bot
                  </Button>
                  <Button onClick={() => openSettings('bot')}>Bot settings</Button>
                </div>
              }
            />
          </div>
        )}
      </div>
    </section>
  )
}
