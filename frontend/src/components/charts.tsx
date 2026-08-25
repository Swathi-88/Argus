import { Table as TableIcon, LineChart as LineChartIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useState } from 'react'

import { Table, Td, Th, cx } from './ui'

/*
  Chart primitives.

  Fixed mark specs, applied everywhere:
  - bars capped at 24px with a 4px rounded data-end, square at the baseline
  - 2px surface-coloured gap between adjacent bars in a group
  - 2px lines, >=8px markers carrying a 2px surface ring
  - hairline solid gridlines one step off the surface, no vertical rules
  - a legend whenever there are two or more series, direct labels used sparingly
  - every chart has a table view, so no value is reachable only through colour
*/

export const SERIES = {
  s1: 'var(--series-1)',
  s2: 'var(--series-2)',
  s3: 'var(--series-3)',
} as const

/** Shared Recharts axis styling. Recessive by design — the data is the ink. */
export const axisProps = {
  tick: { fill: 'var(--text-muted)', fontSize: 11 },
  axisLine: { stroke: 'var(--axis)', strokeWidth: 1 },
  tickLine: false,
} as const

export const gridProps = {
  stroke: 'var(--gridline)',
  strokeWidth: 1,
  vertical: false,
} as const

/** Bar spec: never fills its slot, rounded only at the data end. */
export const barProps = {
  maxBarSize: 24,
  radius: [4, 4, 0, 0] as [number, number, number, number],
}

/** Line spec: 2px stroke, 8px markers ringed in the surface colour. */
export const lineProps = (color: string) => ({
  strokeWidth: 2,
  stroke: color,
  dot: { r: 3, fill: color, stroke: 'var(--surface)', strokeWidth: 2 },
  activeDot: { r: 5, fill: color, stroke: 'var(--surface)', strokeWidth: 2 },
})

/**
 * Chart container with title, legend, and a table toggle.
 *
 * The table is not a nicety: three of the light-mode palette slots sit below 3:1
 * against the surface, and the relief rule for that is visible labels or a table
 * view. This provides the table view for every chart in the app.
 */
export function ChartFrame({
  title,
  subtitle,
  series,
  children,
  table,
  height = 260,
  actions,
  className,
}: {
  title: string
  subtitle?: ReactNode
  /** Omit or pass one entry for a single-series chart — no legend box is drawn. */
  series?: { label: string; color: string }[]
  children: ReactNode
  table?: ReactNode
  height?: number
  actions?: ReactNode
  className?: string
}) {
  const [showTable, setShowTable] = useState(false)
  const showLegend = (series?.length ?? 0) >= 2

  return (
    <section
      className={cx('rounded-xl border border-line bg-surface p-5 shadow-card', className)}
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-primary">
            {title}
          </h3>
          {subtitle && (
            <p className="mt-0.5 max-w-xl text-[12px] leading-snug text-muted">{subtitle}</p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {actions}
          {table && (
            <button
              onClick={() => setShowTable((current) => !current)}
              title={showTable ? 'Show chart' : 'Show values as a table'}
              className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-[11px] font-medium text-muted transition-colors hover:border-line-strong hover:text-primary"
            >
              {showTable ? (
                <LineChartIcon className="size-3" />
              ) : (
                <TableIcon className="size-3" />
              )}
              {showTable ? 'Chart' : 'Table'}
            </button>
          )}
        </div>
      </header>

      {showLegend && (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
          {series!.map((entry) => (
            <span key={entry.label} className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className="h-0.5 w-3.5 rounded-full"
                style={{ background: entry.color }}
              />
              <span className="text-[11.5px] text-secondary">{entry.label}</span>
            </span>
          ))}
        </div>
      )}

      {showTable && table ? (
        <div className="mt-4">{table}</div>
      ) : (
        <div className="mt-4" style={{ height }}>
          {children}
        </div>
      )}
    </section>
  )
}

/** Recharts tooltip content, themed and consistent across every chart. */
export function ChartTooltip({
  active,
  payload,
  label,
  formatter,
  labelFormatter,
}: {
  active?: boolean
  payload?: any[]
  label?: any
  formatter?: (value: any, name: string, entry: any) => ReactNode
  labelFormatter?: (label: any, payload?: any[]) => ReactNode
}) {
  if (!active || !payload?.length) return null

  return (
    <div className="rounded-lg border border-line bg-raised px-2.5 py-2 shadow-pop">
      <p className="mb-1 text-[11px] font-semibold text-primary">
        {labelFormatter ? labelFormatter(label, payload) : label}
      </p>
      <div className="flex flex-col gap-0.5">
        {payload.map((entry, index) => (
          <div key={index} className="flex items-center gap-2 text-[11.5px]">
            <span
              aria-hidden
              className="size-1.5 shrink-0 rounded-full"
              style={{ background: entry.color ?? entry.fill }}
            />
            <span className="text-muted">{entry.name}</span>
            <span className="tnum ml-auto font-medium text-primary">
              {formatter ? formatter(entry.value, entry.name, entry) : entry.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** Two-series comparison table, the shape most of these charts need. */
export function ComparisonTable({
  head,
  rows,
  format = (value) => String(value),
}: {
  head: [string, string, string]
  rows: { key: string; a: number; b: number }[]
  format?: (value: number) => string
}) {
  return (
    <Table>
      <thead>
        <tr>
          <Th>{head[0]}</Th>
          <Th align="right">{head[1]}</Th>
          <Th align="right">{head[2]}</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            <Td className="text-primary">{row.key}</Td>
            <Td align="right" className="tnum">
              {format(row.a)}
            </Td>
            <Td align="right" className="tnum font-medium text-primary">
              {format(row.b)}
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}
