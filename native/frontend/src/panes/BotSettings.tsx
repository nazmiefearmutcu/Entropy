import type {
  BotConfigJSON,
  ConsensusConfigJSON,
  MarketCostConfigJSON,
  MetaResponse,
  RiskOverridesJSON,
  SettingsPatch,
} from '../contract'
import { humanSeconds, timeframeSeconds } from '../format'
import {
  Chip,
  Disclosure,
  NumberField,
  SelectField,
  TextField,
  Toggle,
} from '../ui/controls'
import { CadenceExplainer, Row2, Row3, Section } from './settings-parts'

const VOTE_MODE_HELP: Record<string, string> = {
  adaptive: 'Regime-aware. Trend-follows in trend, fades in range, stands down in chop.',
  trend: 'Always trend-follows, whatever the regime says.',
  mean_revert: 'Always fades extremes, whatever the regime says.',
  legacy:
    'OLD, KNOWN-BROKEN mapping. It is trend-blind and is kept only so past runs can be reproduced. Do not use it for live decisions.',
}

const NORMALIZE_HELP: Record<string, string> = {
  none: 'Raw indicator votes, no rescaling.',
  weights: 'Divide by the total weight of participating indicators.',
  participation: 'Divide by the number of indicators that actually voted.',
}

const RISK_OVERRIDE_FIELDS: {
  key: keyof RiskOverridesJSON
  label: string
  step: number
  suffix?: string
}[] = [
  { key: 'per_trade_pct', label: 'Per trade', step: 0.1, suffix: '%' },
  { key: 'max_concurrent', label: 'Max concurrent', step: 1 },
  { key: 'stop_loss_pct', label: 'Stop loss', step: 0.1, suffix: '%' },
  { key: 'take_profit_pct', label: 'Take profit', step: 0.1, suffix: '%' },
  { key: 'max_total_exposure_pct', label: 'Max exposure', step: 1, suffix: '%' },
  { key: 'max_daily_loss_pct', label: 'Max daily loss', step: 0.1, suffix: '%' },
  { key: 'cooldown_s', label: 'Cooldown', step: 1, suffix: 's' },
  { key: 'min_volatility_pct', label: 'Min volatility', step: 0.05, suffix: '%' },
  { key: 'vol_window_s', label: 'Vol window', step: 1, suffix: 's' },
]

const MARKET_COST_FIELDS: {
  key: keyof MarketCostConfigJSON
  label: string
  /** Which flat Account field a null value inherits. */
  kind: 'fee' | 'slippage'
}[] = [
  { key: 'crypto_spot_fee_bps', label: 'Spot fee', kind: 'fee' },
  { key: 'crypto_spot_slippage_bps', label: 'Spot slippage', kind: 'slippage' },
  { key: 'crypto_futures_fee_bps', label: 'Futures fee', kind: 'fee' },
  { key: 'crypto_futures_slippage_bps', label: 'Futures slippage', kind: 'slippage' },
  { key: 'equity_fee_bps', label: 'Equity fee', kind: 'fee' },
  { key: 'equity_slippage_bps', label: 'Equity slippage', kind: 'slippage' },
]

function WeightSlider({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (v: number) => void
}) {
  return (
    <div className="py-1.5">
      <div className="flex items-baseline justify-between">
        <span className="text-micro uppercase tracking-label text-ink-mute">{label}</span>
        <span className="font-mono tnum text-xs text-ink-dim">{value.toFixed(2)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={2}
        step={0.05}
        value={value}
        aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1.5 w-full"
      />
    </div>
  )
}

/**
 * Bot configuration. The default view is engine + risk + cadence + account; the
 * forty-odd signal parameters live behind disclosures so the front page stays a
 * page rather than a wall of inputs.
 */
export function BotSettings({
  bot,
  meta,
  scannerTf,
  chartInterval,
  resolvedInterval,
  apply,
}: {
  bot: BotConfigJSON
  meta: MetaResponse | null
  scannerTf: string
  chartInterval: string
  resolvedInterval: string
  apply: (patch: SettingsPatch) => void
}) {
  const c = bot.consensus
  const setBot = (patch: Partial<BotConfigJSON>) => apply({ bot: patch })
  const setConsensus = (patch: Partial<ConsensusConfigJSON>) => apply({ bot: { consensus: patch } })
  const setRisk = (patch: Partial<RiskOverridesJSON>) => apply({ bot: { risk_overrides: patch } })

  // /api/meta names profiles "Frosty"/"Medium"/"Extreme" while BotConfig stores
  // the lowercase key ("medium"). Matching on the raw name found nothing, so the
  // description never rendered and the select injected the stored value as a
  // duplicate fourth option. get_profile() lowercases, so the key is canonical.
  const profileKey = (p: { name: string }) => p.name.toLowerCase()
  const profile = meta?.risk_profiles.find((p) => profileKey(p) === bot.risk_profile.toLowerCase())
  const live = bot.mode.toLowerCase() === 'live'
  const effectiveBar = bot.bar_s > 0 ? bot.bar_s : timeframeSeconds(bot.timeframe)

  return (
    <>
      <Section
        title="Engine"
        caption="What the bot is, what it is allowed to risk, and which strategies get a vote."
      >
        <Row2>
          <SelectField
            label="Mode"
            value={bot.mode}
            options={[
              { value: 'paper', label: 'paper — simulated fills' },
              { value: 'live', label: 'live — real orders' },
            ]}
            onChange={(v) => setBot({ mode: v })}
            hint={live ? undefined : 'Paper mode never touches an exchange.'}
          />
          <SelectField
            label="Risk profile"
            value={bot.risk_profile.toLowerCase()}
            options={(meta?.risk_profiles ?? []).map((p) => ({
              value: profileKey(p),
              label: p.name,
            }))}
            onChange={(v) => setBot({ risk_profile: v })}
          />
        </Row2>

        {live && (
          <div className="mt-1 flex items-start gap-2 rounded-[3px] bg-down-soft px-2.5 py-2">
            <Chip tone="loud">live money</Chip>
            <p className="text-xs leading-relaxed text-ink-dim">
              Live mode routes real orders. The sidecar additionally requires
              <span className="font-mono text-ink"> live.enabled</span> and
              <span className="font-mono text-ink"> live.acknowledged_risk</span>, plus exchange
              credentials, which are not editable from this window by design.
            </p>
          </div>
        )}

        {profile && (
          <div className="mt-1.5 rounded-[3px] bg-sunken px-2.5 py-2">
            <p className="text-xs leading-relaxed text-ink-dim">{profile.description}</p>
            <div className="mt-1.5 grid grid-cols-4 gap-x-3 gap-y-1">
              {[
                ['per trade', `${profile.per_trade_pct}%`],
                ['max concurrent', String(profile.max_concurrent)],
                ['stop', `${profile.stop_loss_pct}%`],
                ['target', `${profile.take_profit_pct}%`],
                ['max exposure', `${profile.max_total_exposure_pct}%`],
                ['daily loss cap', `${profile.max_daily_loss_pct}%`],
                ['cooldown', `${profile.cooldown_s}s`],
                ['min volatility', `${profile.min_volatility_pct}%`],
              ].map(([k, v]) => (
                <div key={k}>
                  <div className="text-micro uppercase tracking-label text-ink-faint">{k}</div>
                  <div className="font-mono tnum text-xs text-ink-dim">{v}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-2">
          <div className="mb-1 text-micro uppercase tracking-label text-ink-mute">Strategies</div>
          {(meta?.strategies ?? []).length === 0 && (
            <p className="text-xs text-ink-faint">
              Strategy catalog unavailable — the sidecar did not answer /api/meta.
            </p>
          )}
          <div className="grid grid-cols-2 gap-x-4">
            {(meta?.strategies ?? []).map((s) => {
              const on = bot.strategies.includes(s)
              return (
                <label
                  key={s}
                  className="flex cursor-pointer items-center gap-2 py-1 text-sm text-ink-dim hover:text-ink"
                >
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() =>
                      setBot({
                        strategies: on
                          ? bot.strategies.filter((x) => x !== s)
                          : [...bot.strategies, s],
                      })
                    }
                    className="h-3.5 w-3.5 accent-[var(--accent)]"
                  />
                  <span className="font-mono text-xs">{s}</span>
                </label>
              )
            })}
          </div>
          {bot.strategies.length === 0 && (
            <p className="mt-1 text-xs text-down">
              No strategy selected — the consensus has nothing to vote on and the bot will never
              trade.
            </p>
          )}
        </div>

        <TextField
          label="Symbols"
          value={bot.symbols.join(', ')}
          onCommit={(v) =>
            setBot({
              symbols: v
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          hint="Comma-separated. Leave empty to let the bot follow the watchlist universe."
        />
      </Section>

      <Section
        title="Cadence"
        caption="The bot has its own clock. It is not the scanner timeframe and not the chart candle width."
      >
        <CadenceExplainer
          scannerTf={scannerTf}
          chartInterval={chartInterval}
          resolvedInterval={resolvedInterval}
          botTf={bot.timeframe}
          botBarS={bot.bar_s}
        />
        <Row3>
          <SelectField
            label="Bot timeframe"
            value={bot.timeframe}
            options={(meta?.timeframes ?? []).map((t) => ({ value: t, label: t }))}
            onChange={(v) => setBot({ timeframe: v })}
          />
          <NumberField
            label="bar_s override"
            value={bot.bar_s}
            step={1}
            min={0}
            suffix="s"
            onCommit={(v) => setBot({ bar_s: v })}
            hint={
              bot.bar_s > 0
                ? `overriding: ${humanSeconds(bot.bar_s)}`
                : `0 = follow timeframe (${humanSeconds(effectiveBar)})`
            }
          />
          <div className="py-1.5">
            <div className="mb-1 text-micro uppercase tracking-label text-ink-mute">Warmup</div>
            <div className="flex h-[28px] items-center gap-2">
              <Toggle
                label="Require warmup before trading"
                checked={bot.warmup}
                onChange={(v) => setBot({ warmup: v })}
              />
              <span className="text-xs text-ink-dim">{bot.warmup ? 'required' : 'skipped'}</span>
            </div>
            <p className="mt-1 text-xs text-ink-mute">
              Hold signals until every indicator has filled its lookback.
            </p>
          </div>
        </Row3>
      </Section>

      <Section title="Account" caption="Simulated book parameters for paper mode.">
        <Row3>
          <NumberField
            label="Starting cash"
            value={bot.starting_cash}
            step={1000}
            min={0}
            onCommit={(v) => setBot({ starting_cash: v })}
          />
          <NumberField
            label="Fees"
            value={bot.fee_bps}
            step={0.5}
            min={0}
            suffix="bps"
            onCommit={(v) => setBot({ fee_bps: v })}
          />
          <NumberField
            label="Slippage"
            value={bot.slippage_bps}
            step={0.5}
            min={0}
            suffix="bps"
            onCommit={(v) => setBot({ slippage_bps: v })}
          />
        </Row3>
      </Section>

      <Section
        title="Costs"
        caption="The cost-aware gate layer. When on, realistic per-market fees decide whether a trade is worth taking."
      >
        <div className="py-1.5">
          <div className="flex items-center gap-2">
            <Toggle
              label="Cost-aware trading"
              checked={bot.cost_aware}
              onChange={(v) => setBot({ cost_aware: v })}
            />
            <span className="text-xs text-ink-dim">{bot.cost_aware ? 'on' : 'off'}</span>
          </div>
          <p className="mt-1 text-xs text-ink-mute">
            Off = legacy flat-fee behavior: only the Account fee/slippage apply and every cost gate is
            skipped.
          </p>
        </div>
        <Row2>
          <NumberField
            label="Cost edge multiplier"
            value={bot.cost_edge_mult}
            step={0.1}
            min={0}
            onCommit={(v) => setBot({ cost_edge_mult: v })}
            hint="k in the entry gates mean|r| >= k*C."
          />
          <NumberField
            label="Max cost-to-stop"
            value={bot.max_cost_to_stop}
            step={0.05}
            min={0}
            max={1}
            onCommit={(v) => setBot({ max_cost_to_stop: v })}
            hint="Round-trip cost over stop distance; above this the entry is rejected."
          />
        </Row2>
        <Disclosure
          title="Per-market fees & slippage"
          caption="bps per side; blank inherits the flat Account values"
        >
          <p className="mb-1 text-xs leading-relaxed text-ink-mute">
            A blank (null) field inherits the flat fee/slippage from Account. Editing one writes an
            explicit override for that market.
          </p>
          <Row3>
            {MARKET_COST_FIELDS.map((f) => (
              <NumberField
                key={f.key}
                label={f.label}
                value={bot.market_costs[f.key] ?? (f.kind === 'fee' ? bot.fee_bps : bot.slippage_bps)}
                step={0.5}
                min={0}
                suffix="bps"
                onCommit={(v) =>
                  setBot({ market_costs: { ...bot.market_costs, [f.key]: v } })
                }
              />
            ))}
          </Row3>
        </Disclosure>
      </Section>

      <div className="px-4 pb-4">
        <Disclosure
          title="Advanced — signal logic"
          caption="how votes become a decision"
        >
          <SelectField
            label="Vote mode"
            value={c.vote_mode}
            options={(meta?.vote_modes ?? ['adaptive', 'trend', 'mean_revert', 'legacy']).map(
              (m) => ({
                value: m,
                label: m === 'legacy' ? 'legacy — deprecated, trend-blind' : m,
              }),
            )}
            onChange={(v) => setConsensus({ vote_mode: v })}
            hint={VOTE_MODE_HELP[c.vote_mode] ?? 'Regime handling for indicator votes.'}
            error={
              c.vote_mode === 'legacy'
                ? 'legacy is the old, known-broken mapping: it ignores regime entirely. Keep it only to reproduce past runs.'
                : undefined
            }
          />
          <Row2>
            <SelectField
              label="Normalize"
              value={c.normalize}
              options={(meta?.normalize_modes ?? ['none', 'weights', 'participation']).map((m) => ({
                value: m,
                label: m,
              }))}
              onChange={(v) => setConsensus({ normalize: v })}
              hint={NORMALIZE_HELP[c.normalize]}
            />
            <SelectField
              label="Exit mode"
              value={c.exit_mode}
              options={(meta?.exit_modes ?? []).map((m) => ({ value: m, label: m }))}
              onChange={(v) => setConsensus({ exit_mode: v })}
              hint="How an open position is released."
            />
          </Row2>
          <Row3>
            <NumberField
              label="Threshold"
              value={c.threshold}
              step={0.05}
              onCommit={(v) => setConsensus({ threshold: v })}
              hint="Score needed to act."
            />
            <NumberField
              label="Min participation"
              value={c.min_participation}
              step={0.05}
              onCommit={(v) => setConsensus({ min_participation: v })}
              hint="Share of indicators that must vote."
            />
            <NumberField
              label="Min bars"
              value={c.min_bars}
              step={1}
              min={0}
              onCommit={(v) => setConsensus({ min_bars: v })}
            />
          </Row3>
          <Row2>
            <NumberField
              label="Min hold bars"
              value={c.min_hold_bars}
              step={1}
              min={0}
              onCommit={(v) => setConsensus({ min_hold_bars: v })}
            />
            <NumberField
              label="Cooldown bars"
              value={c.cooldown_bars}
              step={1}
              min={0}
              onCommit={(v) => setConsensus({ cooldown_bars: v })}
            />
          </Row2>
          <div className="mt-2 grid grid-cols-2 gap-x-4">
            <WeightSlider label="EMA weight" value={c.w_ema} onChange={(v) => setConsensus({ w_ema: v })} />
            <WeightSlider
              label="MACD weight"
              value={c.w_macd}
              onChange={(v) => setConsensus({ w_macd: v })}
            />
            <WeightSlider label="RSI weight" value={c.w_rsi} onChange={(v) => setConsensus({ w_rsi: v })} />
            <WeightSlider
              label="Bollinger weight"
              value={c.w_bollinger}
              onChange={(v) => setConsensus({ w_bollinger: v })}
            />
          </div>
        </Disclosure>

        <Disclosure title="Advanced — indicator periods" caption="EMA, MACD, RSI, Bollinger">
          <Row2>
            <NumberField
              label="EMA fast"
              value={c.ema_fast}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ ema_fast: v })}
              hint="Also drives the fast overlay on the chart."
            />
            <NumberField
              label="EMA slow"
              value={c.ema_slow}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ ema_slow: v })}
            />
          </Row2>
          <Row3>
            <NumberField
              label="MACD fast"
              value={c.macd_fast}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ macd_fast: v })}
            />
            <NumberField
              label="MACD slow"
              value={c.macd_slow}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ macd_slow: v })}
            />
            <NumberField
              label="MACD signal"
              value={c.macd_signal}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ macd_signal: v })}
            />
          </Row3>
          <Row3>
            <NumberField
              label="RSI period"
              value={c.rsi_period}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ rsi_period: v })}
            />
            <NumberField
              label="RSI low"
              value={c.rsi_low}
              step={1}
              onCommit={(v) => setConsensus({ rsi_low: v })}
            />
            <NumberField
              label="RSI high"
              value={c.rsi_high}
              step={1}
              onCommit={(v) => setConsensus({ rsi_high: v })}
            />
          </Row3>
          <Row2>
            <NumberField
              label="RSI trend low"
              value={c.rsi_trend_low}
              step={1}
              onCommit={(v) => setConsensus({ rsi_trend_low: v })}
            />
            <NumberField
              label="RSI trend high"
              value={c.rsi_trend_high}
              step={1}
              onCommit={(v) => setConsensus({ rsi_trend_high: v })}
            />
          </Row2>
          <Row3>
            <NumberField
              label="BB period"
              value={c.bb_period}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ bb_period: v })}
            />
            <NumberField
              label="BB std"
              value={c.bb_std}
              step={0.1}
              onCommit={(v) => setConsensus({ bb_std: v })}
            />
            <NumberField
              label="BB low"
              value={c.bb_low}
              step={0.05}
              onCommit={(v) => setConsensus({ bb_low: v })}
            />
          </Row3>
          <Row3>
            <NumberField
              label="BB high"
              value={c.bb_high}
              step={0.05}
              onCommit={(v) => setConsensus({ bb_high: v })}
            />
            <NumberField
              label="BB trend low"
              value={c.bb_trend_low}
              step={0.05}
              onCommit={(v) => setConsensus({ bb_trend_low: v })}
            />
            <NumberField
              label="BB trend high"
              value={c.bb_trend_high}
              step={0.05}
              onCommit={(v) => setConsensus({ bb_trend_high: v })}
            />
          </Row3>
        </Disclosure>

        <Disclosure title="Advanced — regime detection" caption="what makes a market trend or chop">
          <Row3>
            <NumberField
              label="Move floor"
              value={c.move_floor}
              step={0.01}
              onCommit={(v) => setConsensus({ move_floor: v })}
              hint="Minimum move to count as directional."
            />
            <NumberField
              label="Trend ER"
              value={c.trend_er}
              step={0.01}
              onCommit={(v) => setConsensus({ trend_er: v })}
              hint="Efficiency ratio above which it is a trend."
            />
            <NumberField
              label="Regime tilt"
              value={c.regime_tilt}
              step={0.05}
              onCommit={(v) => setConsensus({ regime_tilt: v })}
            />
          </Row3>
          <Row2>
            <NumberField
              label="Regime window"
              value={c.regime_window}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ regime_window: v })}
            />
            <NumberField
              label="Slope lookback"
              value={c.slope_lookback}
              step={1}
              min={1}
              onCommit={(v) => setConsensus({ slope_lookback: v })}
            />
          </Row2>
        </Disclosure>

        <Disclosure
          title="Advanced — risk overrides"
          caption="blank inherits the profile"
        >
          <p className="mb-1 text-xs leading-relaxed text-ink-mute">
            Each row inherits the selected risk profile until you switch it off. An override applies
            to every strategy.
          </p>
          {RISK_OVERRIDE_FIELDS.map((f) => {
            const raw = bot.risk_overrides[f.key]
            const inherit = raw == null
            const fallback = profile ? Number(profile[f.key as keyof typeof profile] ?? 0) : 0
            return (
              <div key={f.key} className="flex items-center gap-3 border-b border-line py-1.5 last:border-0">
                <span className="w-[128px] shrink-0 text-sm text-ink-dim">{f.label}</span>
                <label className="flex shrink-0 items-center gap-1.5">
                  <Toggle
                    label={`Override ${f.label}`}
                    checked={!inherit}
                    onChange={(on) => setRisk({ [f.key]: on ? fallback : null } as Partial<RiskOverridesJSON>)}
                  />
                  <span className="text-micro uppercase tracking-label text-ink-mute">
                    {inherit ? 'inherit' : 'override'}
                  </span>
                </label>
                <div className="flex flex-1 items-center gap-2">
                  <input
                    type="number"
                    aria-label={f.label}
                    disabled={inherit}
                    step={f.step}
                    value={inherit ? '' : String(raw)}
                    placeholder={profile ? String(fallback) : ''}
                    onChange={(e) => {
                      const n = Number(e.target.value)
                      if (Number.isFinite(n)) setRisk({ [f.key]: n } as Partial<RiskOverridesJSON>)
                    }}
                    className="w-full rounded-[3px] border border-line bg-sunken px-2 py-1 font-mono tnum text-sm text-ink disabled:opacity-35"
                  />
                  {f.suffix && <span className="shrink-0 text-xs text-ink-mute">{f.suffix}</span>}
                </div>
              </div>
            )
          })}
        </Disclosure>
      </div>
    </>
  )
}
