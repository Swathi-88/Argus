import {
  ArrowRight,
  Bell,
  BellOff,
  Cpu,
  Database,
  Filter,
  FileClock,
  Radio,
  ScanSearch,
  Sigma,
  Zap,
} from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import {
  Button,
  Callout,
  Card,
  CardHeader,
  Field,
  PageHeader,
  Pill,
  Select,
  StatTile,
  StatusBadge,
  Table,
  Td,
  TextInput,
  Th,
  TierTransition,
  cx,
  useToast,
} from '../components/ui'
import { api } from '../lib/api'
import { humanise, score, shortHash, signed } from '../lib/format'
import type { PipelineStage, PipelineTrace } from '../lib/types'

/**
 * Preset scenarios. Each one is a real FCA enforcement pattern, so the demo
 * shows the engine against the failures it was built for rather than against a
 * made-up input.
 */
const PRESETS = [
  {
    id: 'wealthtek',
    label: 'FCA client-money revocation',
    note: 'The WealthTek pattern — the FS Register would have shown the firm was not permitted to hold client money.',
    event_type: 'CLIENT_MONEY_REVOCATION',
    source: 'FCA_Register',
    subject: 'WealthTek Ltd',
  },
  {
    id: 'sanctions',
    label: 'Sanctions designation',
    note: 'A new listing. Mandatory immediate reassessment; likelihood ratio 50.',
    event_type: 'SANCTIONS_UPDATE',
    source: 'OpenSanctions_API',
    subject: 'Meridian Bullion Trading Ltd',
  },
  {
    id: 'stunt',
    label: 'Police raid',
    note: 'The Stunt & Co pattern — adverse action against a high-value client.',
    event_type: 'POLICE_RAID',
    source: 'NewsAPI',
    subject: 'Stunt & Co Ltd',
  },
  {
    id: 'ubo',
    label: 'Beneficial ownership change',
    note: 'A trigger event under MLR 2017 reg 27 that periodic review routinely misses.',
    event_type: 'UBO_CHANGE',
    source: 'UK_Companies_House',
    subject: 'Kestrel Offshore Holdings Ltd',
  },
  {
    id: 'spike',
    label: 'Transaction spike',
    note: 'Turnover materially above the profile agreed at onboarding.',
    event_type: 'TRANSACTION_SPIKE',
    source: 'Internal_Transaction_Monitoring',
    subject: 'Aldgate Crypto Exchange Ltd',
  },
  {
    id: 'alias',
    label: 'Adverse media, arriving under a trading name',
    note: 'Exercises alias resolution: the feed names the trading name, not the name on file.',
    event_type: 'FRAUD_ALLEGATION',
    source: 'NewsAPI',
    subject: 'Vertem Asset Management',
  },
  {
    id: 'routine',
    label: 'Routine filing (control case)',
    note: 'Should be suppressed by the materiality gate — evidence the engine is not simply alerting on everything.',
    event_type: 'ROUTINE_FILING',
    source: 'UK_Companies_House',
    subject: 'Northwind Freight Services Ltd',
  },
] as const

const STAGE_META: Record<
  string,
  { label: string; icon: typeof ScanSearch; blurb: string }
> = {
  ENTITY_RESOLUTION: {
    label: 'Entity resolution',
    icon: ScanSearch,
    blurb: 'Match the incoming name against customers and registered aliases',
  },
  CLASSIFICATION: {
    label: 'Signal classification',
    icon: Filter,
    blurb: 'Assign category and severity from the rule table',
  },
  PERSIST_EVENT: {
    label: 'Event persisted',
    icon: Database,
    blurb: 'Raw event written before any decision is taken on it',
  },
  MATERIALITY_GATE: {
    label: 'Materiality gate',
    icon: Sigma,
    blurb: 'Relevance, confidence, deduplication, severity floor',
  },
  ALERT_GENERATION: {
    label: 'Alert generated',
    icon: Bell,
    blurb: 'Tier boundary crossed or high-severity event',
  },
  AUDIT_LOG: {
    label: 'Audit trail',
    icon: FileClock,
    blurb: 'Every step appended to the hash chain',
  },
}

const OK_STATUSES = new Set([
  'MATCHED',
  'CLASSIFIED',
  'PERSISTED',
  'MATERIAL',
  'GENERATED',
  'WRITTEN',
])

export function LivePipeline() {
  const toast = useToast()

  const [presetId, setPresetId] = useState<string>(PRESETS[0].id)
  const [entityName, setEntityName] = useState<string>(PRESETS[0].subject)
  const [busy, setBusy] = useState(false)
  const [trace, setTrace] = useState<PipelineTrace | null>(null)

  const preset = PRESETS.find((entry) => entry.id === presetId) ?? PRESETS[0]

  // Each preset names a real seeded customer, so the subject has to move with
  // the scenario — a stale name would resolve to the wrong entity.
  const choosePreset = (id: string) => {
    setPresetId(id)
    const next = PRESETS.find((entry) => entry.id === id)
    if (next) setEntityName(next.subject)
  }

  const inject = async () => {
    setBusy(true)
    setTrace(null)
    try {
      const result = await api.injectEvent({
        entity_name: entityName.trim(),
        event_type: preset.event_type,
        source: preset.source,
        raw_payload: { injected_from: 'analyst_console', preset: preset.id },
      })
      setTrace(result)
      toast.push(
        result.alert_generated ? 'good' : 'info',
        result.alert_generated
          ? `Alert #${result.alert_id} raised in ${result.total_duration_ms.toFixed(0)}ms.`
          : result.materialized
            ? 'Event scored — no tier boundary crossed, so no alert was raised.'
            : 'Event suppressed by the materiality gate. Nothing surfaced to an analyst.',
      )
    } catch (caught) {
      toast.push('critical', caught instanceof Error ? caught.message : 'Injection failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Live pipeline"
        subtitle="Inject an event and watch it move through matching, classification, materiality gating, Bayesian scoring, alerting, and the audit chain."
      />

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ------------------------------------------------------ injector */}
        <Card className="lg:col-span-1">
          <CardHeader
            title="Inject an event"
            subtitle="Presets replicate real FCA enforcement patterns."
          />

          <div className="mt-4 flex flex-col gap-4">
            <Field label="Scenario">
              <Select
                value={presetId}
                onChange={choosePreset}
                options={PRESETS.map((entry) => ({ value: entry.id, label: entry.label }))}
              />
            </Field>

            <p className="-mt-1 text-[11.5px] leading-relaxed text-muted">{preset.note}</p>

            <Field
              label="Entity name as it arrives on the feed"
              hint="Deliberately misspell or vary it to exercise fuzzy matching."
            >
              <TextInput value={entityName} onChange={setEntityName} placeholder="Acme Ltd" />
            </Field>

            <div className="rounded-lg border border-line bg-sunken p-2.5">
              <dl className="flex flex-col gap-1 text-[11.5px]">
                <div className="flex justify-between gap-2">
                  <dt className="text-muted">Event type</dt>
                  <dd className="font-mono text-primary">{preset.event_type}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-muted">Source feed</dt>
                  <dd className="font-mono text-primary">{preset.source}</dd>
                </div>
              </dl>
            </div>

            <Button
              variant="primary"
              icon={<Zap className="size-3.5" />}
              loading={busy}
              disabled={!entityName.trim()}
              onClick={inject}
              className="w-full"
            >
              Inject event
            </Button>

            <p className="text-[11px] leading-relaxed text-muted">
              This writes to the live demo database: a real event row, a real
              risk-score update, and real audit records. Nothing here is
              simulated.
            </p>
          </div>
        </Card>

        {/* --------------------------------------------------------- trace */}
        <div className="flex flex-col gap-6 lg:col-span-2">
          {!trace ? (
            <Card className="grid min-h-64 place-items-center">
              <div className="max-w-sm px-6 text-center">
                <Radio className="mx-auto mb-3 size-6 text-muted" />
                <p className="text-[14px] font-medium text-primary">No event injected yet</p>
                <p className="mt-1 text-[13px] leading-relaxed text-muted">
                  Pick a scenario and inject. Each pipeline stage will appear with
                  its own timing and the artefact it produced.
                </p>
              </div>
            </Card>
          ) : (
            <>
              {/* Outcome summary */}
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatTile
                  label="Pipeline latency"
                  value={`${trace.total_duration_ms.toFixed(0)}ms`}
                  tone="accent"
                  footnote="End to end, including database writes"
                />
                <StatTile
                  label="Entity match"
                  value={`${trace.event.match_confidence.toFixed(0)}%`}
                  footnote={`${trace.event.match_method} · ${
                    trace.event.matched_customer_name ?? 'no customer matched'
                  }`}
                />
                <StatTile
                  label="Materiality"
                  value={trace.materialized ? 'Material' : 'Suppressed'}
                  tone={trace.materialized ? 'serious' : 'good'}
                  footnote={
                    trace.materialized
                      ? 'Passed the gate and rescored the customer'
                      : 'Filtered — never reached an analyst'
                  }
                />
                <StatTile
                  label="Audit records"
                  value={trace.audit_records_written}
                  footnote="Appended to the hash chain"
                />
              </div>

              {/* Stage-by-stage */}
              <Card padded={false}>
                <div className="p-5 pb-3">
                  <CardHeader
                    title="Pipeline stages"
                    subtitle="In execution order, with the time each stage took."
                  />
                </div>
                <ol className="divide-y divide-[var(--border)]">
                  {trace.stages.map((stage, index) => (
                    <StageRow key={`${stage.stage}-${index}`} stage={stage} index={index} />
                  ))}
                </ol>
              </Card>

              {/* Alert, if one was raised */}
              {trace.alert ? (
                <Card>
                  <CardHeader
                    title={`Alert #${trace.alert.id} raised`}
                    subtitle="Surfaced to the analyst queue for disposition."
                    actions={<StatusBadge status={trace.alert.status} />}
                  />
                  <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
                    <div>
                      <p className="text-[11px] text-muted">Tier change</p>
                      <div className="mt-1">
                        <TierTransition
                          from={trace.alert.previous_tier}
                          to={trace.alert.new_tier}
                        />
                      </div>
                    </div>
                    <div>
                      <p className="text-[11px] text-muted">Risk score</p>
                      <p className="tnum mt-1 text-[13px] font-medium text-primary">
                        {score(trace.alert.previous_score)} → {score(trace.alert.new_score)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] text-muted">Log-odds movement</p>
                      <p className="tnum mt-1 text-[13px] font-medium text-primary">
                        {signed(trace.alert.new_log_odds - trace.alert.previous_log_odds, 4)}
                      </p>
                    </div>
                    <Link
                      to={`/customers/${trace.alert.customer_id}/alerts/${trace.alert.id}`}
                      className="ml-auto inline-flex h-9 items-center gap-1.5 rounded-lg bg-[var(--series-1)] px-3.5 text-[13px] font-medium text-white transition-all hover:brightness-110"
                    >
                      Investigate
                      <ArrowRight className="size-3.5" />
                    </Link>
                  </div>

                  <div className="mt-4">
                    <Callout
                      tone={trace.alert.new_tier === 'CRITICAL' ? 'critical' : 'warning'}
                    >
                      {trace.alert.recommended_action}
                    </Callout>
                  </div>
                </Card>
              ) : (
                <Card>
                  <div className="flex items-start gap-3">
                    <BellOff className="mt-0.5 size-4 shrink-0 text-muted" />
                    <div>
                      <p className="text-[13px] font-medium text-primary">
                        No alert raised — and that is the point
                      </p>
                      <p className="mt-1 text-[12.5px] leading-relaxed text-muted">
                        {trace.materialized
                          ? 'The event was material and rescored the customer, but it did not cross a tier boundary and was not high severity, so it updated the score silently instead of consuming analyst attention.'
                          : 'The materiality gate suppressed this event. A system that alerts on everything is a system nobody reads; suppressing routine signal is what makes the queue workable.'}
                      </p>
                    </div>
                  </div>
                </Card>
              )}

              {/* Audit records written by this injection */}
              <Card padded={false}>
                <div className="p-5 pb-3">
                  <CardHeader
                    title="Audit records written"
                    subtitle="Each row is chained to its predecessor by SHA-256 and can never be updated or deleted."
                  />
                </div>
                <Table>
                  <thead>
                    <tr>
                      <Th align="right" className="w-16">
                        Seq
                      </Th>
                      <Th>Action</Th>
                      <Th>Actor</Th>
                      <Th>Record hash</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {trace.audit_records.map((entry) => (
                      <tr key={entry.id}>
                        <Td align="right" className="tnum text-muted">
                          {entry.sequence_no}
                        </Td>
                        <Td className="font-medium text-primary">{entry.action_label}</Td>
                        <Td>
                          <Pill tone={entry.origin === 'ANALYST' ? 'accent' : 'neutral'}>
                            {entry.actor}
                          </Pill>
                        </Td>
                        <Td className="font-mono text-[11px]">
                          {shortHash(entry.record_hash, 20)}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
                <div className="px-4 py-3">
                  <Link
                    to={
                      trace.event.matched_customer_id
                        ? `/audit/${trace.event.matched_customer_id}`
                        : '/audit'
                    }
                    className="inline-flex items-center gap-1.5 text-[12.5px] font-medium"
                    style={{ color: 'var(--ink-accent)' }}
                  >
                    View the full audit trail
                    <ArrowRight className="size-3.5" />
                  </Link>
                </div>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function StageRow({ stage, index }: { stage: PipelineStage; index: number }) {
  const meta = STAGE_META[stage.stage] ?? {
    label: humanise(stage.stage),
    icon: Cpu,
    blurb: '',
  }
  const ok = OK_STATUSES.has(stage.status)

  return (
    <li className="animate-in flex items-start gap-3 px-5 py-3.5">
      <div className="flex flex-col items-center">
        <div
          className="grid size-7 shrink-0 place-items-center rounded-lg"
          style={{ background: ok ? 'var(--wash-accent)' : 'var(--wash-warning)' }}
        >
          <meta.icon
            className="size-3.5"
            style={{ color: ok ? 'var(--ink-accent)' : 'var(--ink-warning)' }}
          />
        </div>
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="tnum text-[11px] text-muted">{index + 1}</span>
          <p className="text-[13px] font-medium text-primary">{meta.label}</p>
          <Pill tone={ok ? 'accent' : 'warning'}>{stage.status}</Pill>
          <span className="tnum ml-auto text-[11.5px] text-muted">
            {stage.duration_ms.toFixed(1)}ms
          </span>
        </div>

        {meta.blurb && <p className="mt-0.5 text-[11.5px] text-muted">{meta.blurb}</p>}

        <StageDetail stage={stage} />
      </div>
    </li>
  )
}

/** Renders the fields that matter per stage, rather than dumping the payload. */
function StageDetail({ stage }: { stage: PipelineStage }) {
  const detail = stage.detail ?? {}

  const rows: [string, string][] = (() => {
    switch (stage.stage) {
      case 'ENTITY_RESOLUTION':
        return [
          ['Query', String(detail.query ?? '—')],
          ['Matched', String(detail.matched_customer_name ?? 'no match')],
          ['Matched against', String(detail.matched_string ?? '—')],
          ['Method / confidence', `${detail.method} · ${Number(detail.confidence).toFixed(1)}%`],
        ]
      case 'CLASSIFICATION':
        return [
          ['Category', String(detail.category)],
          ['Severity', String(detail.severity)],
        ]
      case 'MATERIALITY_GATE':
        return [
          ['Score', `${detail.materiality_score}/100`],
          [
            'Risk score',
            `${score(Number(detail.previous_risk_score))} → ${score(Number(detail.new_risk_score))}`,
          ],
        ]
      case 'ALERT_GENERATION':
        return [
          ['Alert', `#${detail.alert_id}`],
          ['Transition', String(detail.transition)],
        ]
      case 'AUDIT_LOG':
        return [
          ['Records', String(detail.records_written)],
          ['Head hash', shortHash(String(detail.head_hash ?? ''), 16)],
        ]
      default:
        return []
    }
  })()

  const reasons = Array.isArray(detail.reasons) ? (detail.reasons as string[]) : []

  return (
    <>
      {rows.length > 0 && (
        <dl className="mt-2 grid gap-x-5 gap-y-1 sm:grid-cols-2">
          {rows.map(([label, value]) => (
            <div key={label} className="flex min-w-0 items-baseline gap-1.5 text-[11.5px]">
              <dt className="shrink-0 text-muted">{label}</dt>
              <dd className="truncate font-medium text-primary">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      {/* The gate's own stated reasons — the audit-grade explanation of the
          decision, shown verbatim rather than summarised. */}
      {reasons.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {reasons.map((reason, index) => (
            <li
              key={index}
              className={cx(
                'border-l-2 pl-2.5 text-[11.5px] leading-relaxed text-secondary',
              )}
              style={{
                borderColor: /suppress|filter|below|duplicate|unmatched/i.test(reason)
                  ? 'var(--status-warning)'
                  : 'var(--axis)',
              }}
            >
              {reason}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
