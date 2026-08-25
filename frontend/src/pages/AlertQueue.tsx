import {
  ChevronLeft,
  ChevronRight,
  Inbox,
  RefreshCw,
  Search,
  SlidersHorizontal,
} from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SegmentedControl,
  Select,
  Spinner,
  StatTile,
  StatusBadge,
  Table,
  Td,
  Th,
  TierBadge,
  TierTransition,
  cx,
} from '../components/ui'
import { api } from '../lib/api'
import { compact, dateTime, humanise, relativeTime, score, signed } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import type { AlertStatus, Tier } from '../lib/types'

const STATUS_TABS: { value: string; label: string }[] = [
  { value: 'NEW', label: 'Open' },
  { value: 'ESCALATED', label: 'Escalated' },
  { value: 'CONFIRMED', label: 'Confirmed' },
  { value: 'DISMISSED', label: 'Dismissed' },
  { value: 'ALL', label: 'All' },
]

const TIER_OPTIONS = [
  { value: '', label: 'All tiers' },
  { value: 'CRITICAL', label: 'Critical only' },
  { value: 'HIGH', label: 'High only' },
  { value: 'MEDIUM', label: 'Medium only' },
  { value: 'LOW', label: 'Low only' },
]

const PAGE_SIZE = 25

export function AlertQueue() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('NEW')
  const [tier, setTier] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)

  const stats = useAsync(() => api.alertStats(), [])
  const queue = useAsync(
    () =>
      api.alertQueue({
        status,
        tier: tier || undefined,
        search: search.trim() || undefined,
        page,
        size: PAGE_SIZE,
      }),
    [status, tier, search, page],
  )

  const refresh = () => {
    queue.reload()
    stats.reload()
  }

  const total = queue.data?.total ?? 0
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Alert queue"
        subtitle="Reassessment alerts raised by the engine, worst destination tier first, then by the size of the score movement."
        actions={
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshCw className={cx('size-3.5', queue.loading && 'animate-spin')} />}
            onClick={refresh}
          >
            Refresh
          </Button>
        }
      />

      {/* Queue counters. Open work first — it is what the analyst acts on. */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Open alerts"
          value={compact(stats.data?.open_alerts)}
          tone="accent"
          footnote="Awaiting disposition or further information"
        />
        <StatTile
          label="Critical, open"
          value={compact(stats.data?.critical_open)}
          tone="critical"
          footnote="Immediate action required"
        />
        <StatTile
          label="Escalated to MLRO"
          value={compact(stats.data?.escalated)}
          tone="serious"
          footnote="Referred for senior review"
        />
        <StatTile
          label="Total raised"
          value={compact(stats.data?.total_alerts)}
          footnote="Across every disposition"
        />
      </div>

      {/* Filters in one row above the table, per the interaction spec. */}
      <div className="flex flex-wrap items-center gap-3">
        <SegmentedControl
          value={status}
          onChange={(value) => {
            setStatus(value)
            setPage(1)
          }}
          options={STATUS_TABS.map((tab) => ({
            ...tab,
            count:
              tab.value === 'ALL'
                ? stats.data?.total_alerts
                : stats.data?.by_status?.[tab.value],
          }))}
        />

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search
              aria-hidden
              className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted"
            />
            <input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setPage(1)
              }}
              placeholder="Search customer…"
              className="h-9 w-52 rounded-lg border border-line-strong bg-surface pr-2.5 pl-8 text-[13px] text-primary placeholder:text-muted"
            />
          </div>
          <div className="flex items-center gap-1.5">
            <SlidersHorizontal aria-hidden className="size-3.5 text-muted" />
            <Select
              value={tier}
              onChange={(value) => {
                setTier(value)
                setPage(1)
              }}
              options={TIER_OPTIONS}
              className="w-36"
            />
          </div>
        </div>
      </div>

      <Card padded={false}>
        {queue.loading && !queue.data ? (
          <Spinner label="Loading queue…" />
        ) : queue.error ? (
          <ErrorState message={queue.error} onRetry={queue.reload} />
        ) : !queue.data?.alerts.length ? (
          <EmptyState
            icon={<Inbox className="size-6" />}
            title="Nothing in this view"
            body={
              status === 'NEW'
                ? 'No open alerts. Inject an event from the Live pipeline view to raise one.'
                : 'No alerts match the current filters.'
            }
          />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th className="w-12" align="right">
                    #
                  </Th>
                  <Th>Customer</Th>
                  <Th>Tier change</Th>
                  <Th align="right">Risk score</Th>
                  <Th>Primary trigger</Th>
                  <Th>Status</Th>
                  <Th align="right">Raised</Th>
                </tr>
              </thead>
              <tbody>
                {queue.data.alerts.map((alert) => (
                  <tr
                    key={alert.id}
                    tabIndex={0}
                    onClick={() => navigate(`/customers/${alert.customer_id}/alerts/${alert.id}`)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        navigate(`/customers/${alert.customer_id}/alerts/${alert.id}`)
                      }
                    }}
                    className="cursor-pointer transition-colors hover:bg-[var(--surface-hover)]"
                  >
                    <Td align="right" className="tnum text-muted">
                      {alert.priority_rank}
                    </Td>

                    <Td>
                      <div className="min-w-0">
                        <p className="truncate font-medium text-primary">
                          {alert.customer_name ?? `Customer ${alert.customer_id}`}
                        </p>
                        <p className="truncate text-[11.5px] text-muted">
                          {alert.customer_industry} · {alert.customer_country}
                        </p>
                      </div>
                    </Td>

                    <Td>
                      <TierTransition from={alert.previous_tier} to={alert.new_tier} />
                    </Td>

                    <Td align="right">
                      <div className="tnum">
                        <span className="font-medium text-primary">
                          {score(alert.new_score)}
                        </span>
                        <span className="ml-1.5 text-[11.5px] text-muted">
                          from {score(alert.previous_score)}
                        </span>
                      </div>
                      <p className="tnum text-[11px] text-muted">
                        log-odds {signed(alert.new_log_odds - alert.previous_log_odds)}
                      </p>
                    </Td>

                    <Td>
                      <div className="flex min-w-0 flex-col gap-1">
                        <span className="truncate font-medium text-primary">
                          {humanise(alert.trigger_event_type)}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <SeverityPill severity={alert.trigger_event_severity} />
                          <span className="truncate text-[11px] text-muted">
                            {alert.trigger_source ?? '—'}
                          </span>
                        </span>
                      </div>
                    </Td>

                    <Td>
                      <StatusBadge status={alert.status} />
                    </Td>

                    <Td align="right">
                      <span className="text-[12.5px] whitespace-nowrap text-secondary">
                        {relativeTime(alert.created_at)}
                      </span>
                      <p className="text-[11px] whitespace-nowrap text-muted">
                        {dateTime(alert.created_at)}
                      </p>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>

            <footer className="flex items-center justify-between gap-3 px-4 py-3">
              <p className="tnum text-[12px] text-muted">
                {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of{' '}
                {total.toLocaleString('en-GB')}
              </p>
              <div className="flex items-center gap-1.5">
                <Button
                  variant="ghost"
                  size="sm"
                  icon={<ChevronLeft className="size-3.5" />}
                  disabled={page <= 1}
                  onClick={() => setPage((current) => current - 1)}
                >
                  Previous
                </Button>
                <span className="tnum px-1 text-[12px] text-muted">
                  {page} / {lastPage}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={page >= lastPage}
                  onClick={() => setPage((current) => current + 1)}
                >
                  Next
                  <ChevronRight className="size-3.5" />
                </Button>
              </div>
            </footer>
          </>
        )}
      </Card>
    </div>
  )
}

function SeverityPill({ severity }: { severity: string | null }) {
  if (!severity) return <Pill tone="neutral">Unknown</Pill>
  const tone =
    severity === 'CRITICAL'
      ? 'critical'
      : severity === 'HIGH'
        ? 'serious'
        : severity === 'MEDIUM'
          ? 'warning'
          : 'neutral'
  return <Pill tone={tone as any}>{severity}</Pill>
}

/** Re-exported for the investigation page's header. */
export { SeverityPill }
export type { AlertStatus, Tier }
