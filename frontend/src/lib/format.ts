import type { Tier } from './types'

/** Tier → status role. Tier order is severity order, so the mapping is direct. */
export const TIER_STATUS: Record<Tier, 'good' | 'warning' | 'serious' | 'critical'> = {
  LOW: 'good',
  MEDIUM: 'warning',
  HIGH: 'serious',
  CRITICAL: 'critical',
}

export const TIER_ORDER: Tier[] = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

export const percent = (value: number | null | undefined, digits = 1) =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`

export const score = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`

export const signed = (value: number | null | undefined, digits = 2) => {
  if (value === null || value === undefined) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}`
}

/** 1,284 / 12.9K / 4.2M — compact enough for a stat tile. */
export const compact = (value: number | null | undefined) => {
  if (value === null || value === undefined) return '—'
  if (Math.abs(value) < 10_000) return value.toLocaleString('en-GB')
  if (Math.abs(value) < 1_000_000) return `${(value / 1_000).toFixed(1)}K`
  return `${(value / 1_000_000).toFixed(2)}M`
}

/** Days, at whatever precision makes the number readable. */
export const days = (value: number | null | undefined) => {
  if (value === null || value === undefined) return '—'
  if (value === 0) return 'instant'
  if (value < 1 / 24) return `${Math.round(value * 24 * 60)} min`
  if (value < 1) return `${(value * 24).toFixed(1)} hrs`
  return `${value.toFixed(1)} days`
}

export const dateTime = (iso: string | null | undefined) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export const dateOnly = (iso: string | null | undefined) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

export const timeOnly = (iso: string | null | undefined) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export const relativeTime = (iso: string | null | undefined) => {
  if (!iso) return '—'
  const deltaSeconds = (Date.now() - new Date(iso).getTime()) / 1000
  if (deltaSeconds < 60) return 'just now'
  if (deltaSeconds < 3600) return `${Math.floor(deltaSeconds / 60)}m ago`
  if (deltaSeconds < 86_400) return `${Math.floor(deltaSeconds / 3600)}h ago`
  if (deltaSeconds < 2_592_000) return `${Math.floor(deltaSeconds / 86_400)}d ago`
  return dateOnly(iso)
}

/** SANCTIONS_MATCH → Sanctions match */
export const humanise = (value: string | null | undefined) => {
  if (!value) return '—'
  const lower = value.replace(/_/g, ' ').toLowerCase()
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

export const shortHash = (hash: string | null | undefined, length = 10) =>
  !hash ? '—' : `${hash.slice(0, length)}…`

export const currency = (value: number | null | undefined) => {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('en-GB', {
    style: 'currency',
    currency: 'GBP',
    notation: value >= 1_000_000 ? 'compact' : 'standard',
    maximumFractionDigits: value >= 1_000_000 ? 1 : 0,
  }).format(value)
}
