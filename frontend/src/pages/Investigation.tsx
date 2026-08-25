import {
  ArrowLeft,
  ArrowUpRight,
  CheckCircle2,
  FileClock,
  Flag,
  HelpCircle,
  Info,
  ShieldAlert,
  XCircle,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ChartFrame, ChartTooltip, SERIES, axisProps, gridProps, lineProps } from '../components/charts'
import {
  Button,
  Callout,
  Card,
  CardHeader,
  ErrorState,
  Pill,
  Spinner,
  StatTile,
  StatusBadge,
  Table,
  Td,
  Textarea,
  Th,
  TierBadge,
  TierTransition,
  cx,
  useToast,
} from '../components/ui'
import { api } from '../lib/api'
import { PERM, useAuth } from '../lib/auth'
import { currency, dateOnly, dateTime, humanise, score, signed } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import type { Disposition } from '../lib/types'

/** The four analyst dispositions, with the permission each one needs. */
const ACTIONS: {
  action: Disposition
  label: string
  description: string
  icon: typeof CheckCircle2
  variant: 'primary' | 'secondary' | 'danger' | 'success'
  permission: string
}[] = [
  {
    action: 'CONFIRMED',
    label: 'Confirm reassessment',
    description: 'Accept the new tier and the recommended action.',
    icon: CheckCircle2,
    variant: 'success',
    permission: PERM.actAlerts,
  },
  {
    action: 'DISMISSED',
    label: 'Dismiss',
    description: 'The trigger does not change this customer’s risk.',
    icon: XCircle,
    variant: 'secondary',
    permission: PERM.actAlerts,
  },
  {
    action: 'INFO_REQUESTED',
    label: 'Request info',
    description: 'Hold pending further information from the relationship team.',
    icon: HelpCircle,
    variant: 'secondary',
    permission: PERM.actAlerts,
  },
  {
    action: 'ESCALATED',
    label: 'Escalate to MLRO',
    description: 'Refer for senior compliance review. Manager only.',
    icon: Flag,
    variant: 'danger',
    permission: PERM.escalateAlerts,
  },
]

export function Investigation() {
  const { customerId, alertId } = useParams()
  const navigate = useNavigate()
  const { user, can } = useAuth()
  const toast = useToast()

  const id = Number(customerId)

  const customer = useAsync(() => api.customer(id), [id])
  const timeline = useAsync(() => api.riskTimeline(id), [id])
  const explanation = useAsync(() => api.riskExplanation(id), [id])
  const alert = useAsync(() => api.alert(Number(alertId)), [alertId], {
    enabled: Boolean(alertId),
  })

  const [notes, setNotes] = useState('')
  const [pending, setPending] = useState<Disposition | null>(null)

  const act = async (action: Disposition) => {
    if (!alertId) return
    setPending(action)
    try {
      const updated = await api.actOnAlert(Number(alertId), action, notes.trim() || undefined)
      alert.setData(updated)
      setNotes('')
      toast.push('good', `Alert #${updated.id} marked ${action.replace(/_/g, ' ').toLowerCase()}.`)
    } catch (caught) {
      toast.push('critical', caught instanceof Error ? caught.message : 'Action failed')
    } finally {
      setPending(null)
    }
  }

  // The line chart plots score against a real time axis, so the gaps between
  // events read as elapsed time rather than as evenly-spaced steps.
  const chartData = useMemo(
    () =>
      (timeline.data?.points ?? []).map((point) => ({
        ...point,
        t: new Date(point.timestamp).getTime(),
        scorePct: point.risk_score * 100,
      })),
    [timeline.data],
  )

  if (customer.loading && !customer.data) return <Spinner label="Loading customer…" />
  if (customer.error) return <ErrorState message={customer.error} onRetry={customer.reload} />
  if (!customer.data) return null

  const record = customer.data
  const disposed = alert.data && alert.data.status !== 'NEW'

  return (
    <div className="flex flex-col gap-6">
      {/* ------------------------------------------------------------ header */}
      <div>
        <button
          onClick={() => navigate(-1)}
          className="mb-3 inline-flex items-center gap-1.5 text-[12.5px] text-muted transition-colors hover:text-primary"
        >
          <ArrowLeft className="size-3.5" />
          Back to queue
        </button>

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[22px] font-semibold tracking-[-0.02em] text-primary">
                {record.name}
              </h1>
              <TierBadge tier={record.risk_tier} />
              {record.is_sanctioned && <Pill tone="critical">Sanctioned</Pill>}
              {record.is_pep && <Pill tone="warning">PEP</Pill>}
            </div>
            <p className="mt-1 text-[13px] text-muted">
              {record.type} · {record.industry} · {record.country} · onboarded{' '}
              {dateOnly(record.onboarding_date)} · customer #{record.id}
            </p>
          </div>

          <Link
            to={`/audit/${record.id}`}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3.5 text-[13px] font-medium text-primary transition-colors hover:bg-[var(--surface-hover)]"
          >
            <FileClock className="size-3.5" />
            Audit trail
          </Link>
        </div>
      </div>

      {/* --------------------------------------------------------- stat row */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Current risk score"
          value={score(record.risk_score)}
          footnote={`P(high risk) · log-odds ${timeline.data?.current_log_odds?.toFixed(3) ?? '—'}`}
        />
        <StatTile
          label="Onboarding prior"
          value={score(explanation.data?.prior_score)}
          footnote={`Before any event evidence · log-odds ${
            explanation.data?.prior_log_odds?.toFixed(3) ?? '—'
          }`}
        />
        <StatTile
          label="Material events scored"
          value={explanation.data?.event_history.length ?? '—'}
          footnote="Passed the materiality gate and moved the score"
        />
        <StatTile
          label="Expected turnover"
          value={currency(record.expected_turnover)}
          footnote="Agreed at onboarding"
        />
      </div>

      {/* ---------------------------------------------- risk evolution chart */}
      <ChartFrame
        title="Risk score evolution"
        subtitle="Each vertex is one material event. The bands are the engine's tier boundaries, so a crossing is visible rather than inferred."
        height={300}
        table={
          <Table>
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Event</Th>
                <Th align="right">LR</Th>
                <Th align="right">Δ log-odds</Th>
                <Th align="right">Log-odds</Th>
                <Th align="right">Risk score</Th>
                <Th>Tier</Th>
              </tr>
            </thead>
            <tbody>
              {(timeline.data?.points ?? []).map((point) => (
                <tr key={point.sequence}>
                  <Td className="whitespace-nowrap">{dateTime(point.timestamp)}</Td>
                  <Td className="text-primary">{point.label}</Td>
                  <Td align="right" className="tnum">
                    {point.likelihood_ratio?.toFixed(1) ?? '—'}
                  </Td>
                  <Td align="right" className="tnum">
                    {point.log_odds_delta === null ? '—' : signed(point.log_odds_delta, 4)}
                  </Td>
                  <Td align="right" className="tnum">
                    {point.log_odds.toFixed(4)}
                  </Td>
                  <Td align="right" className="tnum font-medium text-primary">
                    {score(point.risk_score)}
                  </Td>
                  <Td>
                    <TierBadge tier={point.risk_tier} size="sm" />
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        }
      >
        {timeline.loading ? (
          <Spinner />
        ) : chartData.length < 2 ? (
          <div className="grid h-full place-items-center px-6 text-center">
            <p className="max-w-sm text-[13px] text-muted">
              Only the onboarding prior exists for this customer — no material
              event has moved the score yet, so there is no line to draw.
            </p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: -12 }}>
              {/* Tier bands, drawn behind the line as the quietest possible
                  reference. Labelled once at the right edge, not repeated. */}
              <ReferenceArea
                y1={80}
                y2={100}
                fill="var(--status-critical)"
                fillOpacity={0.07}
                strokeOpacity={0}
              />
              <ReferenceArea
                y1={50}
                y2={80}
                fill="var(--status-serious)"
                fillOpacity={0.07}
                strokeOpacity={0}
              />
              <ReferenceArea
                y1={20}
                y2={50}
                fill="var(--status-warning)"
                fillOpacity={0.07}
                strokeOpacity={0}
              />

              <CartesianGrid {...gridProps} />

              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                tickFormatter={(value) =>
                  new Date(value).toLocaleDateString('en-GB', {
                    month: 'short',
                    year: '2-digit',
                  })
                }
                {...axisProps}
              />
              <YAxis
                domain={[0, 100]}
                ticks={[0, 20, 50, 80, 100]}
                tickFormatter={(value) => `${value}%`}
                width={48}
                {...axisProps}
              />

              {[20, 50, 80].map((boundary) => (
                <ReferenceLine
                  key={boundary}
                  y={boundary}
                  stroke="var(--axis)"
                  strokeWidth={1}
                />
              ))}

              <Tooltip
                cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }}
                content={
                  <ChartTooltip
                    labelFormatter={(_label, payload) => {
                      const point = payload?.[0]?.payload
                      return point ? `${point.label} · ${dateTime(point.timestamp)}` : ''
                    }}
                    formatter={(value: number) => `${value.toFixed(1)}%`}
                  />
                }
              />

              <Line
                type="monotone"
                dataKey="scorePct"
                name="P(high risk)"
                {...lineProps(SERIES.s1)}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </ChartFrame>

      <div className="grid gap-6 lg:grid-cols-5">
        {/* --------------------------------------- why: log-odds breakdown */}
        <Card className="lg:col-span-3" padded={false}>
          <div className="p-5 pb-0">
            <CardHeader
              title="Why this score"
              subtitle="Every piece of evidence, and the log-odds it contributed. The prior plus the deltas equals the current score exactly."
            />
          </div>

          {explanation.loading ? (
            <Spinner />
          ) : explanation.error ? (
            <ErrorState message={explanation.error} onRetry={explanation.reload} />
          ) : explanation.data ? (
            <div className="mt-4">
              {/* Step 0 — the prior, broken into its own contributions */}
              <div className="border-y border-line bg-sunken px-5 py-3.5">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="text-[12.5px] font-semibold text-primary">
                    Onboarding prior
                  </p>
                  <p className="tnum text-[12.5px] font-medium text-primary">
                    {score(explanation.data.prior_score)}
                  </p>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(
                    explanation.data.onboarding_math_breakdown.contributions,
                  ).map(([factor, weight]) => (
                    <span
                      key={factor}
                      className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-1.5 py-0.5 text-[11px]"
                    >
                      <span className="text-muted">{humanise(factor)}</span>
                      <span className="tnum font-medium text-primary">
                        {signed(weight, 2)}
                      </span>
                    </span>
                  ))}
                </div>
                <p className="tnum mt-2 text-[11px] text-muted">
                  logit = {explanation.data.prior_log_odds.toFixed(4)} → P ={' '}
                  {score(explanation.data.prior_score)}
                </p>
              </div>

              {/* Each subsequent event, as a contribution row */}
              {explanation.data.event_history.length === 0 ? (
                <p className="px-5 py-6 text-[13px] text-muted">
                  No material events have been scored against this customer. The
                  score is the onboarding prior.
                </p>
              ) : (
                <ol className="divide-y divide-[var(--border)]">
                  {explanation.data.event_history.map((step) => (
                    <ContributionRow
                      key={`${step.step}-${step.event_id}`}
                      step={step}
                      maxDelta={Math.max(
                        ...explanation.data!.event_history.map((s) => s.log_odds_delta),
                      )}
                    />
                  ))}
                </ol>
              )}

              <div className="flex items-baseline justify-between gap-3 border-t border-line bg-sunken px-5 py-3.5">
                <p className="text-[12.5px] font-semibold text-primary">Current score</p>
                <div className="text-right">
                  <p className="tnum text-[14px] font-semibold text-primary">
                    {score(explanation.data.current_risk_score)}
                  </p>
                  <p className="tnum text-[11px] text-muted">
                    log-odds {explanation.data.current_log_odds.toFixed(4)}
                  </p>
                </div>
              </div>
            </div>
          ) : null}
        </Card>

        {/* -------------------------------------------- analyst action panel */}
        <div className="flex flex-col gap-6 lg:col-span-2">
          {alertId ? (
            alert.loading && !alert.data ? (
              <Card>
                <Spinner />
              </Card>
            ) : alert.error ? (
              <Card>
                <ErrorState message={alert.error} onRetry={alert.reload} />
              </Card>
            ) : alert.data ? (
              <Card>
                <CardHeader
                  title={`Alert #${alert.data.id}`}
                  subtitle={`Raised ${dateTime(alert.data.created_at)}`}
                  actions={<StatusBadge status={alert.data.status} />}
                />

                <dl className="mt-4 flex flex-col gap-2.5 text-[12.5px]">
                  <Row label="Tier change">
                    <TierTransition
                      from={alert.data.previous_tier}
                      to={alert.data.new_tier}
                    />
                  </Row>
                  <Row label="Score">
                    <span className="tnum">
                      {score(alert.data.previous_score)} → {score(alert.data.new_score)}
                    </span>
                  </Row>
                  <Row label="Primary trigger">
                    <span className="text-right">
                      {humanise(alert.data.trigger_event_type)}
                      <span className="block text-[11px] text-muted">
                        {alert.data.trigger_source} · event #{alert.data.trigger_event_id}
                      </span>
                    </span>
                  </Row>
                </dl>

                <div className="mt-4">
                  <Callout tone={alert.data.new_tier === 'CRITICAL' ? 'critical' : 'info'}>
                    {alert.data.recommended_action}
                  </Callout>
                </div>

                {disposed ? (
                  <div className="mt-4 rounded-lg border border-line bg-sunken p-3">
                    <p className="text-[12.5px] font-medium text-primary">
                      Already dispositioned
                    </p>
                    <p className="mt-0.5 text-[12px] text-muted">
                      Marked <strong>{alert.data.status.replace(/_/g, ' ').toLowerCase()}</strong>{' '}
                      {alert.data.updated_at ? `on ${dateTime(alert.data.updated_at)}` : ''}.
                      Recorded permanently in the audit trail.
                    </p>
                    {typeof alert.data.breakdown?.analyst_notes === 'string' && (
                      <p className="mt-2 border-l-2 border-[var(--axis)] pl-2.5 text-[12px] text-secondary italic">
                        “{alert.data.breakdown.analyst_notes as string}”
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="mt-4 flex flex-col gap-3">
                    <div>
                      <p className="mb-1.5 text-[12px] font-medium text-secondary">
                        Decision note{' '}
                        <span className="font-normal text-muted">(optional)</span>
                      </p>
                      <Textarea
                        value={notes}
                        onChange={setNotes}
                        rows={3}
                        placeholder="Rationale, evidence reviewed, next steps…"
                      />
                    </div>

                    <div className="flex flex-col gap-1.5">
                      {ACTIONS.map((entry) => {
                        const allowed = can(entry.permission)
                        return (
                          <Button
                            key={entry.action}
                            variant={entry.variant}
                            icon={<entry.icon className="size-3.5" />}
                            loading={pending === entry.action}
                            disabled={!allowed || pending !== null}
                            title={
                              allowed
                                ? entry.description
                                : `${user?.role} does not hold the ${entry.permission} permission`
                            }
                            onClick={() => act(entry.action)}
                            className="w-full justify-start"
                          >
                            {entry.label}
                          </Button>
                        )
                      })}
                    </div>

                    {!can(PERM.actAlerts) && (
                      <Callout tone="warning" title="Read-only role">
                        {user?.role} can review this alert but cannot dispose of it.
                        Independence of the assurance function is enforced by the
                        API, not just hidden in the UI — the attempt would be
                        refused and recorded.
                      </Callout>
                    )}
                    {can(PERM.actAlerts) && !can(PERM.escalateAlerts) && (
                      <p className="text-[11.5px] leading-relaxed text-muted">
                        Escalation to MLRO requires a Manager. Everything else is
                        available to you.
                      </p>
                    )}
                  </div>
                )}
              </Card>
            ) : null
          ) : (
            <Card>
              <CardHeader
                title="No alert selected"
                subtitle="You are viewing this customer's risk history directly. Open the customer from the alert queue to record a decision."
              />
            </Card>
          )}

          {/* Recommendation, independent of any single alert */}
          {explanation.data && (
            <Card>
              <CardHeader title="Standing recommendation" />
              <div className="mt-3">
                <Callout
                  tone={
                    explanation.data.current_risk_tier === 'CRITICAL'
                      ? 'critical'
                      : explanation.data.current_risk_tier === 'HIGH'
                        ? 'warning'
                        : 'info'
                  }
                >
                  {explanation.data.recommendation}
                </Callout>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right font-medium text-primary">{children}</dd>
    </div>
  )
}

/**
 * One evidence row. The bar is a magnitude cue for the log-odds contribution,
 * scaled against the largest contribution on this customer — so the reader sees
 * at a glance which event actually moved the score.
 */
function ContributionRow({
  step,
  maxDelta,
}: {
  step: {
    step: number
    event_id: number
    event_type: string
    event_category: string
    event_severity: string
    source: string
    likelihood_ratio_LR: number
    log_odds_delta: number
    previous_score: number
    new_score: number
  }
  maxDelta: number
}) {
  const width = maxDelta > 0 ? Math.max(4, (step.log_odds_delta / maxDelta) * 100) : 0
  const tone =
    step.event_severity === 'CRITICAL'
      ? 'critical'
      : step.event_severity === 'HIGH'
        ? 'serious'
        : step.event_severity === 'MEDIUM'
          ? 'warning'
          : 'neutral'

  return (
    <li className="px-5 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-primary">
            {humanise(step.event_type)}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Pill tone={tone as any}>{step.event_severity}</Pill>
            <span className="text-[11px] text-muted">
              {humanise(step.event_category)} · {step.source} · event #{step.event_id}
            </span>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <p className="tnum text-[13px] font-semibold" style={{ color: 'var(--series-1)' }}>
            {signed(step.log_odds_delta, 3)}
          </p>
          <p className="tnum text-[11px] text-muted">LR {step.likelihood_ratio_LR.toFixed(1)}</p>
        </div>
      </div>

      {/* Magnitude bar: 4px rounded data-end, square at the baseline. */}
      <div className="mt-2 flex items-center gap-2.5">
        <div className="h-1.5 flex-1 overflow-hidden rounded-l-[1px] bg-sunken">
          <div
            className="h-full rounded-r-[4px]"
            style={{ width: `${width}%`, background: 'var(--series-1)' }}
          />
        </div>
        <p className="tnum shrink-0 text-[11px] text-muted">
          {score(step.previous_score)} → {score(step.new_score)}
        </p>
      </div>
    </li>
  )
}
