import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  FileClock,
  FileJson,
  Link2,
  Lock,
  ShieldCheck,
  ShieldX,
  Sheet,
  UserRound,
  Cpu,
} from 'lucide-react'
import { useState } from 'react'
import { useParams } from 'react-router-dom'

import {
  Button,
  Callout,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SegmentedControl,
  Spinner,
  StatTile,
  cx,
  useToast,
} from '../components/ui'
import { api } from '../lib/api'
import { PERM, useAuth } from '../lib/auth'
import { compact, dateTime, shortHash, timeOnly } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import type { AuditRecord } from '../lib/types'

const PAGE_SIZE = 50

export function AuditTrail() {
  const { customerId } = useParams()
  const { user, can } = useAuth()
  const toast = useToast()

  const scopedId = customerId ? Number(customerId) : undefined

  const [origin, setOrigin] = useState('ALL')
  const [page, setPage] = useState(1)
  const [exporting, setExporting] = useState<'json' | 'csv' | null>(null)

  const trail = useAsync(
    () =>
      api.auditTrail({
        customer_id: scopedId,
        origin: origin === 'ALL' ? undefined : origin,
        page,
        size: PAGE_SIZE,
      }),
    [scopedId, origin, page],
  )

  // Verification and the immutability proof are gated on audit:verify, so an
  // Analyst sees the trail but not the control evidence.
  const verification = useAsync(() => api.verifyChain(scopedId), [scopedId], {
    enabled: can(PERM.verifyAudit),
  })
  const proof = useAsync(() => api.immutabilityProof(), [], {
    enabled: can(PERM.verifyAudit),
  })

  const exportTrail = async (format: 'json' | 'csv') => {
    setExporting(format)
    try {
      await api.exportAudit(format, scopedId, origin === 'ALL' ? undefined : origin)
      toast.push('good', `Audit trail exported as ${format.toUpperCase()}. The export is itself recorded.`)
      trail.reload()
    } catch (caught) {
      toast.push('critical', caught instanceof Error ? caught.message : 'Export failed')
    } finally {
      setExporting(null)
    }
  }

  const total = trail.data?.total ?? 0
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Audit trail"
        subtitle={
          scopedId
            ? `Every system action and analyst decision for ${trail.data?.customer_name ?? `customer #${scopedId}`}, oldest first.`
            : 'Every system action and analyst decision across the engine, oldest first.'
        }
        actions={
          can(PERM.exportAudit) ? (
            <>
              <Button
                variant="secondary"
                size="sm"
                icon={<FileJson className="size-3.5" />}
                loading={exporting === 'json'}
                onClick={() => exportTrail('json')}
              >
                JSON
              </Button>
              <Button
                variant="secondary"
                size="sm"
                icon={<Sheet className="size-3.5" />}
                loading={exporting === 'csv'}
                onClick={() => exportTrail('csv')}
              >
                CSV
              </Button>
            </>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-[12px] text-muted">
              <Lock className="size-3.5" />
              {user?.role} cannot export
            </span>
          )
        }
      />

      {/* The immutability guarantee, evidenced rather than asserted. */}
      {can(PERM.verifyAudit) && (
        <div className="grid gap-3 lg:grid-cols-3">
          <IntegrityCard verification={verification} />
          <ImmutabilityCard proof={proof} />
          <StatTile
            label="Records in scope"
            value={compact(total)}
            footnote={
              scopedId
                ? 'Audit records naming this customer'
                : 'Every audit record in the chain'
            }
          />
        </div>
      )}

      {!can(PERM.verifyAudit) && (
        <Callout tone="info" title="Read access">
          You can read the trail. Chain verification, the immutability proof, and
          export are reserved for Manager and Auditor roles.
        </Callout>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <SegmentedControl
          value={origin}
          onChange={(value) => {
            setOrigin(value)
            setPage(1)
          }}
          options={[
            { value: 'ALL', label: 'All actions' },
            { value: 'SYSTEM', label: 'System' },
            { value: 'ANALYST', label: 'Analyst decisions' },
          ]}
        />
        <p className="text-[12px] text-muted">
          Append-only. No row here can be updated or deleted, including by the
          application's own database account.
        </p>
      </div>

      <Card padded={false}>
        {trail.loading && !trail.data ? (
          <Spinner label="Loading audit trail…" />
        ) : trail.error ? (
          <ErrorState message={trail.error} onRetry={trail.reload} />
        ) : !trail.data?.records.length ? (
          <EmptyState
            icon={<FileClock className="size-6" />}
            title="No audit records"
            body="Nothing has been recorded for this scope yet."
          />
        ) : (
          <>
            <ol className="divide-y divide-[var(--border)]">
              {trail.data.records.map((entry) => (
                <AuditRow key={entry.id} entry={entry} />
              ))}
            </ol>

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

/* ------------------------------------------------------------ proof panels */

function IntegrityCard({
  verification,
}: {
  verification: ReturnType<typeof useAsync<import('../lib/types').ChainVerification>>
}) {
  const data = verification.data
  const valid = data?.chain_valid

  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[12px] font-medium text-muted">Hash chain integrity</p>
          <p className="mt-1.5 flex items-center gap-1.5 text-[17px] font-semibold tracking-[-0.01em] text-primary">
            {verification.loading ? (
              '…'
            ) : valid ? (
              <>
                <ShieldCheck className="size-4" style={{ color: 'var(--status-good)' }} />
                Verified
              </>
            ) : (
              <>
                <ShieldX className="size-4" style={{ color: 'var(--status-critical)' }} />
                {data ? `${data.breaks.length} break(s)` : 'Unavailable'}
              </>
            )}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={verification.reload}>
          Re-verify
        </Button>
      </div>

      {data && (
        <div className="mt-2 flex flex-col gap-1 text-[11px] text-muted">
          <p className="tnum">
            {compact(data.records_verified)} records recomputed · SHA-256 chained
          </p>
          <p className="tnum flex items-center gap-1">
            <Link2 className="size-3" />
            head {shortHash(data.head_hash, 14)}
          </p>
          {data.unchained_legacy_records > 0 && (
            <p>
              {data.unchained_legacy_records} pre-Phase-4 record(s) predate the chain and
              are excluded.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function ImmutabilityCard({
  proof,
}: {
  proof: ReturnType<typeof useAsync<import('../lib/types').ImmutabilityProof>>
}) {
  const [open, setOpen] = useState(false)
  const data = proof.data

  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[12px] font-medium text-muted">Append-only enforcement</p>
          <p className="mt-1.5 flex items-center gap-1.5 text-[17px] font-semibold tracking-[-0.01em] text-primary">
            {proof.loading ? (
              '…'
            ) : data?.enforced ? (
              <>
                <Lock className="size-4" style={{ color: 'var(--status-good)' }} />
                Enforced
              </>
            ) : (
              <>
                <ShieldX className="size-4" style={{ color: 'var(--status-critical)' }} />
                Not enforced
              </>
            )}
          </p>
        </div>
        <button
          onClick={() => setOpen((current) => !current)}
          className="text-muted transition-colors hover:text-primary"
          aria-label="Show proof detail"
        >
          <ChevronDown className={cx('size-4 transition-transform', open && 'rotate-180')} />
        </button>
      </div>

      <p className="mt-2 text-[11px] leading-snug text-muted">
        {data?.method === 'trigger_inspection'
          ? (data.note ??
            'Verified by inspecting the installed triggers.')
          : 'A real UPDATE and DELETE were attempted and rolled back. PostgreSQL refused both.'}
      </p>

      {open && data && (
        <div className="mt-3 flex flex-col gap-2 border-t border-line pt-3">
          {(['update', 'delete'] as const).map((operation) => (
            <div key={operation}>
              <p className="flex items-center gap-1.5 text-[11px] font-semibold text-primary">
                {data[`${operation}_blocked`] ? (
                  <ShieldCheck className="size-3" style={{ color: 'var(--status-good)' }} />
                ) : (
                  <ShieldX className="size-3" style={{ color: 'var(--status-critical)' }} />
                )}
                {operation.toUpperCase()} blocked
              </p>
              <p className="mt-0.5 font-mono text-[10.5px] leading-snug break-words text-muted">
                {data[`${operation}_error`]}
              </p>
            </div>
          ))}
          <div>
            <p className="text-[11px] font-semibold text-primary">Triggers installed</p>
            <ul className="mt-0.5 flex flex-col gap-0.5">
              {data.triggers_present.map((name) => (
                <li key={name} className="font-mono text-[10.5px] text-muted">
                  {name}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}

/* --------------------------------------------------------------- trail row */

const IMPORTANT_ACTIONS = new Set([
  'ALERT_GENERATED',
  'ANALYST_ACTION_CONFIRMED',
  'ANALYST_ACTION_DISMISSED',
  'ANALYST_ACTION_ESCALATED',
  'ANALYST_ACTION_INFO_REQUESTED',
  'AUDIT_TRAIL_EXPORTED',
  'PERMISSION_DENIED',
])

function AuditRow({ entry }: { entry: AuditRecord }) {
  const [open, setOpen] = useState(false)
  const isAnalyst = entry.origin === 'ANALYST'
  const emphasised = IMPORTANT_ACTIONS.has(entry.action)

  const tone =
    entry.action === 'PERMISSION_DENIED'
      ? 'critical'
      : entry.action === 'ALERT_GENERATED'
        ? 'serious'
        : isAnalyst
          ? 'accent'
          : 'neutral'

  return (
    <li className={cx('px-4 py-3', emphasised && 'bg-sunken')}>
      <div className="flex items-start gap-3">
        {/* Origin marker: icon + label, so system vs analyst never relies on hue */}
        <div
          className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md"
          style={{ background: `var(--wash-${tone})` }}
          title={isAnalyst ? 'Analyst decision' : 'System action'}
        >
          {isAnalyst ? (
            <UserRound
              className="size-3"
              style={{ color: tone === 'neutral' ? 'var(--text-secondary)' : `var(--ink-${tone})` }}
            />
          ) : (
            <Cpu
              className="size-3"
              style={{ color: tone === 'neutral' ? 'var(--text-secondary)' : `var(--ink-${tone})` }}
            />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <p className="text-[13px] font-medium text-primary">{entry.action_label}</p>
            <Pill tone={isAnalyst ? 'accent' : 'neutral'}>{entry.origin}</Pill>
            <span className="tnum text-[11px] text-muted">seq {entry.sequence_no ?? '—'}</span>
          </div>

          <p className="mt-0.5 text-[11.5px] text-muted">
            {entry.actor ?? 'SYSTEM'}
            {entry.actor_role && entry.actor_role !== 'SYSTEM' && ` (${entry.actor_role})`} ·{' '}
            {entry.entity_type} #{entry.entity_id}
            {entry.customer_id !== null && ` · customer #${entry.customer_id}`}
          </p>

          {open && (
            <div className="mt-2.5 flex flex-col gap-2">
              {entry.details && Object.keys(entry.details).length > 0 && (
                <pre className="overflow-x-auto rounded-lg border border-line bg-surface p-2.5 font-mono text-[11px] leading-relaxed text-secondary">
                  {JSON.stringify(entry.details, null, 2)}
                </pre>
              )}
              <div className="flex flex-col gap-0.5 font-mono text-[10.5px] break-all text-muted">
                <p>prev {entry.prev_hash ?? '—'}</p>
                <p>hash {entry.record_hash ?? '—'}</p>
              </div>
            </div>
          )}
        </div>

        <div className="shrink-0 text-right">
          <p className="tnum text-[12px] whitespace-nowrap text-secondary">
            {timeOnly(entry.created_at)}
          </p>
          <p className="text-[10.5px] whitespace-nowrap text-muted">
            {dateTime(entry.created_at).split(',')[0]}
          </p>
          <button
            onClick={() => setOpen((current) => !current)}
            className="mt-0.5 text-[11px] text-muted transition-colors hover:text-primary"
          >
            {open ? 'Hide' : 'Detail'}
          </button>
        </div>
      </div>
    </li>
  )
}
