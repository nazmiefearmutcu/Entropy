import { useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Icon } from './Icon'

/* --------------------------------------------------------------- button */

type Variant = 'primary' | 'default' | 'ghost' | 'danger' | 'up' | 'down'

const VARIANT: Record<Variant, string> = {
  primary: 'bg-accent text-black font-semibold hover:brightness-110 active:brightness-95',
  default: 'bg-raised text-ink hover:bg-hover active:bg-activefill',
  ghost: 'bg-transparent text-ink-dim hover:bg-raised hover:text-ink',
  danger: 'bg-down text-white font-semibold hover:brightness-110 active:brightness-95',
  up: 'bg-up-soft text-up hover:bg-up hover:text-black font-semibold',
  down: 'bg-down-soft text-down hover:bg-down hover:text-white font-semibold',
}

export function Button({
  children,
  onClick,
  variant = 'default',
  size = 'md',
  disabled,
  title,
  type = 'button',
  className = '',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: Variant
  size?: 'sm' | 'md'
  disabled?: boolean
  title?: string
  type?: 'button' | 'submit'
  className?: string
}) {
  const pad = size === 'sm' ? 'h-6 px-2 text-xs' : 'h-7 px-3 text-sm'
  return (
    <button
      type={type}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center justify-center gap-1.5 rounded-[3px] ${pad} ${
        VARIANT[variant]
      } transition-colors duration-150 disabled:opacity-35 disabled:pointer-events-none ${className}`}
    >
      {children}
    </button>
  )
}

export function IconButton({
  icon,
  label,
  onClick,
  active,
  size = 24,
  className = '',
}: {
  icon: Parameters<typeof Icon>[0]['name']
  label: string
  onClick?: () => void
  active?: boolean
  size?: number
  className?: string
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      style={{ width: size, height: size }}
      className={`inline-flex items-center justify-center rounded-[3px] transition-colors duration-150 ${
        active ? 'bg-accent-soft text-accent' : 'text-ink-mute hover:bg-raised hover:text-ink'
      } ${className}`}
    >
      <Icon name={icon} />
    </button>
  )
}

/* ------------------------------------------------------------------ kbd */

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-[15px] min-w-[15px] items-center justify-center rounded-[2px] border border-line-strong bg-sunken px-1 font-ui text-micro text-ink-mute">
      {children}
    </kbd>
  )
}

/* ----------------------------------------------------------------- dots */

export function Dot({
  tone,
  pulse,
  className = '',
}: {
  tone: 'up' | 'down' | 'accent' | 'info' | 'mute'
  pulse?: boolean
  className?: string
}) {
  const bg = {
    up: 'bg-up',
    down: 'bg-down',
    accent: 'bg-accent',
    info: 'bg-info',
    mute: 'bg-ink-faint',
  }[tone]
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-[6px] w-[6px] rounded-full ${bg} ${pulse ? 'pulse-dot' : ''} ${className}`}
    />
  )
}

/* ----------------------------------------------------------------- chip */

export function Chip({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode
  tone?: 'neutral' | 'up' | 'down' | 'accent' | 'info' | 'loud'
  title?: string
}) {
  const cls = {
    neutral: 'bg-raised text-ink-dim',
    up: 'bg-up-soft text-up',
    down: 'bg-down-soft text-down',
    accent: 'bg-accent-soft text-accent',
    info: 'bg-info-soft text-info',
    loud: 'bg-down text-white font-bold',
  }[tone]
  return (
    <span
      title={title}
      className={`inline-flex h-[17px] items-center rounded-[2px] px-1.5 text-micro font-semibold uppercase tracking-label ${cls}`}
    >
      {children}
    </span>
  )
}

/* ----------------------------------------------------------- segmented */

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  pending,
  size = 'md',
}: {
  options: { value: T; label: string; title?: string }[]
  value: T
  onChange: (v: T) => void
  label: string
  pending?: boolean
  size?: 'sm' | 'md'
}) {
  const h = size === 'sm' ? 'h-[20px] text-micro' : 'h-[22px] text-xs'
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={`inline-flex items-center gap-px rounded-[3px] bg-sunken p-px ${
        pending ? 'opacity-60' : ''
      }`}
    >
      {options.map((o) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            title={o.title ?? o.label}
            onClick={() => onChange(o.value)}
            className={`${h} rounded-[2px] px-2 font-mono tnum font-medium transition-colors duration-150 ${
              on
                ? 'bg-accent text-black'
                : 'text-ink-mute hover:bg-raised hover:text-ink'
            }`}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

/* -------------------------------------------------------------- toggle */

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-[16px] w-[28px] shrink-0 items-center rounded-full transition-colors duration-150 disabled:opacity-40 ${
        checked ? 'bg-accent' : 'bg-line-strong'
      }`}
    >
      <span
        className={`absolute h-[12px] w-[12px] rounded-full bg-black transition-transform duration-150 ${
          checked ? 'translate-x-[14px]' : 'translate-x-[2px]'
        }`}
      />
    </button>
  )
}

/* --------------------------------------------------------------- field */

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  inline,
}: {
  label: string
  hint?: string
  error?: string
  children: ReactNode
  htmlFor?: string
  inline?: boolean
}) {
  if (inline) {
    return (
      <div className="py-1.5">
        <div className="flex items-center justify-between gap-3">
          <label htmlFor={htmlFor} className="text-sm text-ink">
            {label}
          </label>
          {children}
        </div>
        {hint && <p className="mt-0.5 max-w-[46ch] text-xs text-ink-mute">{hint}</p>}
        {error && <p className="mt-0.5 text-xs text-down">{error}</p>}
      </div>
    )
  }
  return (
    <div className="py-1.5">
      <label htmlFor={htmlFor} className="mb-1 block text-micro uppercase tracking-label text-ink-mute">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 max-w-[52ch] text-xs text-ink-mute">{hint}</p>}
      {error && <p className="mt-1 text-xs text-down">{error}</p>}
    </div>
  )
}

const INPUT_CLS =
  'w-full rounded-[3px] border border-line bg-sunken px-2 py-1 font-mono tnum text-sm text-ink placeholder:text-ink-faint hover:border-line-strong focus:border-accent focus:outline-none disabled:opacity-40'

export function TextField({
  label,
  value,
  onCommit,
  hint,
  error,
  placeholder,
  disabled,
  mono = true,
}: {
  label: string
  value: string
  onCommit: (v: string) => void
  hint?: string
  error?: string
  placeholder?: string
  disabled?: boolean
  mono?: boolean
}) {
  const id = useId()
  const [draft, setDraft] = useState(value)
  const lastProp = useRef(value)
  if (lastProp.current !== value) {
    lastProp.current = value
    if (draft !== value) setDraft(value)
  }
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <input
        id={id}
        value={draft}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          if (draft !== value) onCommit(draft)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.currentTarget.blur()
          } else if (e.key === 'Escape') {
            setDraft(value)
          }
        }}
        className={`${INPUT_CLS} ${mono ? '' : 'font-ui'}`}
      />
    </Field>
  )
}

export function NumberField({
  label,
  value,
  onCommit,
  hint,
  error,
  step = 1,
  min,
  max,
  suffix,
  disabled,
}: {
  label: string
  value: number
  onCommit: (v: number) => void
  hint?: string
  error?: string
  step?: number
  min?: number
  max?: number
  suffix?: string
  disabled?: boolean
}) {
  const id = useId()
  const [draft, setDraft] = useState(String(value))
  const lastProp = useRef(value)
  if (lastProp.current !== value) {
    lastProp.current = value
    if (Number(draft) !== value) setDraft(String(value))
  }
  const commit = () => {
    const n = Number(draft)
    if (!Number.isFinite(n)) {
      setDraft(String(value))
      return
    }
    if (n !== value) onCommit(n)
  }
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          inputMode="decimal"
          value={draft}
          step={step}
          min={min}
          max={max}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur()
            else if (e.key === 'Escape') setDraft(String(value))
          }}
          className={INPUT_CLS}
        />
        {suffix && <span className="shrink-0 text-xs text-ink-mute">{suffix}</span>}
      </div>
    </Field>
  )
}

export function SelectField({
  label,
  value,
  options,
  onChange,
  hint,
  error,
  disabled,
}: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
  hint?: string
  error?: string
  disabled?: boolean
}) {
  const id = useId()
  const known = options.some((o) => o.value === value)
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <div className="relative">
        <select
          id={id}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          className={`${INPUT_CLS} appearance-none pr-7`}
        >
          {!known && <option value={value}>{value || '(unset)'}</option>}
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-ink-mute">
          <Icon name="chevron-down" size={12} />
        </span>
      </div>
    </Field>
  )
}

/* ---------------------------------------------------------- disclosure */

export function Disclosure({
  title,
  caption,
  children,
  defaultOpen = false,
}: {
  title: string
  caption?: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="mt-3 rounded-[3px] bg-sunken">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left hover:bg-raised"
      >
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={12} className="text-ink-mute" />
        <span className="text-sm font-semibold text-ink">{title}</span>
        {caption && <span className="ml-auto text-xs text-ink-mute">{caption}</span>}
      </button>
      {open && <div className="border-t border-line px-2.5 pb-3 pt-1">{children}</div>}
    </div>
  )
}

/* ------------------------------------------------------------ resizer */

export function Resizer({
  axis,
  onDrag,
  label,
}: {
  axis: 'x' | 'y'
  onDrag: (delta: number) => void
  label: string
}) {
  const last = useRef<number | null>(null)
  const horizontal = axis === 'x'
  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation={horizontal ? 'vertical' : 'horizontal'}
      tabIndex={0}
      onPointerDown={(e) => {
        last.current = horizontal ? e.clientX : e.clientY
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={(e) => {
        if (last.current == null) return
        const cur = horizontal ? e.clientX : e.clientY
        onDrag(cur - last.current)
        last.current = cur
      }}
      onPointerUp={(e) => {
        last.current = null
        e.currentTarget.releasePointerCapture(e.pointerId)
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 24 : 8
        if (horizontal && e.key === 'ArrowLeft') onDrag(-step)
        else if (horizontal && e.key === 'ArrowRight') onDrag(step)
        else if (!horizontal && e.key === 'ArrowUp') onDrag(-step)
        else if (!horizontal && e.key === 'ArrowDown') onDrag(step)
        else return
        e.preventDefault()
      }}
      className={`group relative z-10 shrink-0 bg-base transition-colors duration-150 hover:bg-accent ${
        horizontal ? 'w-px cursor-col-resize' : 'h-px cursor-row-resize'
      }`}
    >
      <span
        className={`absolute ${
          horizontal ? '-inset-x-[3px] inset-y-0' : '-inset-y-[3px] inset-x-0'
        }`}
      />
    </div>
  )
}
