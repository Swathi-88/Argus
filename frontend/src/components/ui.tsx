import { AlertTriangle, Check, ChevronDown, Info, Loader2, ShieldAlert, X } from 'lucide-react'
import type { ReactNode } from 'react'
import { createContext, useCallback, useContext, useMemo, useState } from 'react'

import { TIER_STATUS } from '../lib/format'
import type { Tier } from '../lib/types'

export const cx = (...parts: (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(' ')

/* -------------------------------------------------------------- containers */

export function Card({
  children,
  className,
  padded = true,
}: {
  children: ReactNode
  className?: string
  padded?: boolean
}) {
  return (
    <section
      className={cx(
        'rounded-xl border border-line bg-surface shadow-card',
        padded && 'p-5',
        className,
      )}
    >
      {children}
    </section>
  )
}

export function CardHeader({
  title,
  subtitle,
  actions,
  className,
}: {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <header className={cx('flex items-start justify-between gap-4', className)}>
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-primary">{title}</h2>
        {subtitle && <p className="mt-0.5 text-[13px] leading-snug text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[22px] font-semibold tracking-[-0.02em] text-primary">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-[13px] text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  )
}

/* ------------------------------------------------------------------ badges */

/**
 * Risk tier chip. The wash carries emphasis, the dot carries the hue, and the
 * tier's own name is always spelled out — so a reader who cannot separate the
 * hues still gets the value from the text.
 */
export function TierBadge({ tier, size = 'md' }: { tier: Tier; size?: 'sm' | 'md' }) {
  const status = TIER_STATUS[tier]
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-md font-medium whitespace-nowrap',
        size === 'sm' ? 'px-1.5 py-0.5 text-[11px]' : 'px-2 py-1 text-[12px]',
      )}
      style={{ background: `var(--wash-${status})`, color: `var(--ink-${status})` }}
    >
      <span
        aria-hidden
        className="size-1.5 rounded-full"
        style={{ background: `var(--status-${status})` }}
      />
      {tier}
    </span>
  )
}

/** Tier transition, e.g. MEDIUM → HIGH. The arrow makes direction explicit. */
export function TierTransition({ from, to }: { from: Tier; to: Tier }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <TierBadge tier={from} size="sm" />
      <span aria-hidden className="text-muted">
        →
      </span>
      <span className="sr-only">changed to</span>
      <TierBadge tier={to} size="sm" />
    </span>
  )
}

const STATUS_STYLE: Record<string, { wash: string; ink: string }> = {
  NEW: { wash: 'var(--wash-accent)', ink: 'var(--ink-accent)' },
  CONFIRMED: { wash: 'var(--wash-good)', ink: 'var(--ink-good)' },
  DISMISSED: { wash: 'var(--wash-neutral)', ink: 'var(--text-secondary)' },
  ESCALATED: { wash: 'var(--wash-critical)', ink: 'var(--ink-critical)' },
  INFO_REQUESTED: { wash: 'var(--wash-warning)', ink: 'var(--ink-warning)' },
}

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? {
    wash: 'var(--wash-neutral)',
    ink: 'var(--text-secondary)',
  }
  return (
    <span
      className="inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium tracking-wide whitespace-nowrap"
      style={{ background: style.wash, color: style.ink }}
    >
      {status.replace(/_/g, ' ')}
    </span>
  )
}

export function Pill({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: 'neutral' | 'accent' | 'good' | 'warning' | 'serious' | 'critical'
  className?: string
}) {
  const ink = tone === 'neutral' ? 'var(--text-secondary)' : `var(--ink-${tone})`
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium',
        className,
      )}
      style={{ background: `var(--wash-${tone})`, color: ink }}
    >
      {children}
    </span>
  )
}

/* ----------------------------------------------------------------- buttons */

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary:
    'bg-[var(--series-1)] text-white hover:brightness-[1.08] active:brightness-95 shadow-card',
  secondary:
    'bg-surface text-primary border border-line-strong hover:bg-[var(--surface-hover)]',
  ghost: 'text-secondary hover:bg-[var(--surface-hover)] hover:text-primary',
  danger:
    'bg-[var(--status-critical)] text-white hover:brightness-[1.08] active:brightness-95 shadow-card',
  success:
    'bg-[var(--status-good)] text-white hover:brightness-[1.08] active:brightness-95 shadow-card',
}

export function Button({
  children,
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon,
  disabled,
  title,
  onClick,
  type = 'button',
  className,
}: {
  children?: ReactNode
  variant?: ButtonVariant
  size?: 'sm' | 'md'
  loading?: boolean
  icon?: ReactNode
  disabled?: boolean
  title?: string
  onClick?: () => void
  type?: 'button' | 'submit'
  className?: string
}) {
  return (
    <button
      type={type}
      title={title}
      disabled={disabled || loading}
      onClick={onClick}
      className={cx(
        'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-all',
        'disabled:cursor-not-allowed disabled:opacity-45',
        size === 'sm' ? 'h-8 px-2.5 text-[12px]' : 'h-9 px-3.5 text-[13px]',
        BUTTON_VARIANT[variant],
        className,
      )}
    >
      {loading ? <Loader2 className="size-3.5 animate-spin" /> : icon}
      {children}
    </button>
  )
}

/* ------------------------------------------------------------------ inputs */

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[12px] font-medium text-secondary">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-muted">{hint}</span>}
    </label>
  )
}

const CONTROL =
  'h-9 w-full rounded-lg border border-line-strong bg-surface px-2.5 text-[13px] text-primary ' +
  'placeholder:text-muted transition-colors hover:border-[var(--axis)]'

export function TextInput({
  value,
  onChange,
  placeholder,
  type = 'text',
  autoComplete,
  autoFocus,
  className,
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: string
  autoComplete?: string
  autoFocus?: boolean
  className?: string
}) {
  return (
    <input
      type={type}
      value={value}
      autoComplete={autoComplete}
      autoFocus={autoFocus}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className={cx(CONTROL, className)}
    />
  )
}

export function NumberInput({
  value,
  onChange,
  min,
  max,
  step,
}: {
  value: number
  onChange: (value: number) => void
  min?: number
  max?: number
  step?: number
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={(event) => onChange(Number(event.target.value))}
      className={cx(CONTROL, 'tnum')}
    />
  )
}

export function Select({
  value,
  onChange,
  options,
  className,
}: {
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  className?: string
}) {
  return (
    <div className={cx('relative', className)}>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={cx(CONTROL, 'cursor-pointer appearance-none pr-8')}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-muted"
      />
    </div>
  )
}

export function Textarea({
  value,
  onChange,
  placeholder,
  rows = 3,
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  rows?: number
}) {
  return (
    <textarea
      value={value}
      rows={rows}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className="w-full resize-y rounded-lg border border-line-strong bg-surface px-2.5 py-2 text-[13px] leading-relaxed text-primary placeholder:text-muted"
    />
  )
}

/** Segmented control — used where the option set is small and worth showing. */
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T
  onChange: (value: T) => void
  options: { value: T; label: string; count?: number }[]
}) {
  return (
    <div
      role="tablist"
      className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-sunken p-0.5"
    >
      {options.map((option) => {
        const selected = option.value === value
        return (
          <button
            key={option.value}
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(option.value)}
            className={cx(
              'inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px] font-medium transition-all',
              selected
                ? 'bg-surface text-primary shadow-card'
                : 'text-muted hover:text-secondary',
            )}
          >
            {option.label}
            {option.count !== undefined && (
              <span className="tnum text-[11px] text-muted">{option.count}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ tables */

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx('overflow-x-auto', className)}>
      <table className="w-full border-collapse text-left">{children}</table>
    </div>
  )
}

export function Th({
  children,
  align = 'left',
  className,
}: {
  children?: ReactNode
  align?: 'left' | 'right' | 'center'
  className?: string
}) {
  return (
    <th
      scope="col"
      className={cx(
        'border-b border-line px-3 py-2 text-[11px] font-semibold tracking-wider text-muted uppercase',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        className,
      )}
    >
      {children}
    </th>
  )
}

export function Td({
  children,
  align = 'left',
  className,
}: {
  children?: ReactNode
  align?: 'left' | 'right' | 'center'
  className?: string
}) {
  return (
    <td
      className={cx(
        'border-b border-line px-3 py-2.5 align-middle text-[13px] text-secondary',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        className,
      )}
    >
      {children}
    </td>
  )
}

/* --------------------------------------------------------------- stat tile */

/**
 * Stat tile: label · value · optional delta · optional footnote.
 * The delta's colour reflects direction × whether up is good, and always ships
 * with an arrow glyph so the sign is not colour-only.
 */
export function StatTile({
  label,
  value,
  delta,
  deltaGood,
  footnote,
  tone,
  className,
}: {
  label: string
  value: ReactNode
  delta?: string
  deltaGood?: boolean
  footnote?: ReactNode
  tone?: 'good' | 'warning' | 'serious' | 'critical' | 'accent'
  className?: string
}) {
  return (
    <div
      className={cx(
        'rounded-xl border border-line bg-surface p-4 shadow-card',
        className,
      )}
    >
      <div className="flex items-center gap-1.5">
        {tone && (
          <span
            aria-hidden
            className="size-1.5 rounded-full"
            style={{
              background: tone === 'accent' ? 'var(--series-1)' : `var(--status-${tone})`,
            }}
          />
        )}
        <p className="text-[12px] font-medium text-muted">{label}</p>
      </div>
      <p className="mt-1.5 text-[26px] leading-none font-semibold tracking-[-0.02em] text-primary">
        {value}
      </p>
      {delta && (
        <p
          className="mt-1.5 text-[12px] font-medium"
          style={{ color: deltaGood ? 'var(--ink-good)' : 'var(--ink-critical)' }}
        >
          {deltaGood ? '↑' : '↓'} {delta}
        </p>
      )}
      {footnote && <p className="mt-1.5 text-[11px] leading-snug text-muted">{footnote}</p>}
    </div>
  )
}

/* ------------------------------------------------------------------ states */

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-14 text-muted">
      <Loader2 className="size-4 animate-spin" />
      {label && <span className="text-[13px]">{label}</span>}
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon?: ReactNode
  title: string
  body?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
      {icon && <div className="mb-1 text-muted">{icon}</div>}
      <p className="text-[14px] font-medium text-primary">{title}</p>
      {body && <p className="max-w-md text-[13px] leading-relaxed text-muted">{body}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <ShieldAlert className="size-5" style={{ color: 'var(--status-critical)' }} />
      <p className="text-[14px] font-medium text-primary">Something went wrong</p>
      <p className="max-w-md text-[13px] leading-relaxed text-muted">{message}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} className="mt-2">
          Try again
        </Button>
      )}
    </div>
  )
}

export function Callout({
  tone = 'info',
  title,
  children,
}: {
  tone?: 'info' | 'good' | 'warning' | 'critical'
  title?: string
  children: ReactNode
}) {
  const map = {
    info: { wash: 'var(--wash-accent)', ink: 'var(--ink-accent)', Icon: Info },
    good: { wash: 'var(--wash-good)', ink: 'var(--ink-good)', Icon: Check },
    warning: { wash: 'var(--wash-warning)', ink: 'var(--ink-warning)', Icon: AlertTriangle },
    critical: { wash: 'var(--wash-critical)', ink: 'var(--ink-critical)', Icon: ShieldAlert },
  }[tone]

  return (
    <div className="flex gap-2.5 rounded-lg p-3" style={{ background: map.wash }}>
      <map.Icon className="mt-0.5 size-4 shrink-0" style={{ color: map.ink }} />
      <div className="min-w-0 text-[12.5px] leading-relaxed">
        {title && (
          <p className="font-semibold" style={{ color: map.ink }}>
            {title}
          </p>
        )}
        <div className="text-secondary">{children}</div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ toasts */

type Toast = { id: number; tone: 'good' | 'critical' | 'info'; message: string }

const ToastContext = createContext<{
  push: (tone: Toast['tone'], message: string) => void
}>({ push: () => {} })

export const useToast = () => useContext(ToastContext)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const push = useCallback((tone: Toast['tone'], message: string) => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, tone, message }])
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id))
    }, 5200)
  }, [])

  const value = useMemo(() => ({ push }), [push])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed right-5 bottom-5 z-50 flex w-[min(24rem,calc(100vw-2.5rem))] flex-col gap-2"
      >
        {toasts.map((toast) => {
          const ink =
            toast.tone === 'good'
              ? 'var(--ink-good)'
              : toast.tone === 'critical'
                ? 'var(--ink-critical)'
                : 'var(--ink-accent)'
          const Icon =
            toast.tone === 'good' ? Check : toast.tone === 'critical' ? ShieldAlert : Info
          return (
            <div
              key={toast.id}
              className="animate-in pointer-events-auto flex items-start gap-2.5 rounded-xl border border-line bg-raised p-3 shadow-pop"
            >
              <Icon className="mt-0.5 size-4 shrink-0" style={{ color: ink }} />
              <p className="min-w-0 flex-1 text-[13px] leading-snug text-primary">
                {toast.message}
              </p>
              <button
                aria-label="Dismiss"
                onClick={() => setToasts((c) => c.filter((t) => t.id !== toast.id))}
                className="text-muted transition-colors hover:text-primary"
              >
                <X className="size-3.5" />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}
