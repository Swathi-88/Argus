import { Activity, ArrowRight, Lock } from 'lucide-react'
import { useState } from 'react'

import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useAsync } from '../lib/useAsync'
import { Button, Callout, Field, TextInput, cx } from '../components/ui'

const ROLE_BLURB: Record<string, string> = {
  ANALYST: 'Works the queue, disposes of alerts',
  MANAGER: 'Escalates, exports, runs evaluation',
  AUDITOR: 'Read-only assurance across the system',
}

export function SignIn() {
  const { signIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Published by the API so the demo accounts are discoverable without the
  // README open beside the screen. Passwords are not returned — they are filled
  // in from the documented demo defaults below.
  const roles = useAsync(() => api.roles(), [])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await signIn(username.trim(), password)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Sign-in failed')
    } finally {
      setBusy(false)
    }
  }

  const fill = (name: string) => {
    setUsername(name)
    setPassword(DEMO_PASSWORDS[name] ?? '')
    setError(null)
  }

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      {/* Left: the pitch. A compliance buyer needs to know what this is in one
          screen, so the problem statement sits beside the form. */}
      <div
        className="hidden flex-col justify-between p-12 lg:flex"
        style={{ background: 'var(--sidebar)', color: 'var(--sidebar-text)' }}
      >
        <div className="flex items-center gap-2.5">
          <div
            className="grid size-9 place-items-center rounded-lg"
            style={{ background: 'var(--series-1)' }}
          >
            <Activity className="size-4.5 text-white" strokeWidth={2.5} />
          </div>
          <div>
            <p className="text-[14px] leading-tight font-semibold">Risk Trigger Engine</p>
            <p className="text-[12px] leading-tight" style={{ color: 'var(--sidebar-muted)' }}>
              Continuous KYC reassessment
            </p>
          </div>
        </div>

        <div className="max-w-md">
          <h1 className="text-[30px] leading-[1.15] font-semibold tracking-[-0.025em]">
            Periodic review finds the risk
            <span style={{ color: 'var(--series-2)' }}> 32 days late.</span>
          </h1>
          <p
            className="mt-4 text-[14px] leading-relaxed"
            style={{ color: 'var(--sidebar-muted)' }}
          >
            A fixed 90-day cycle reassesses customers on a calendar, not on
            evidence. This engine reassesses on the event — resolving the entity,
            gating for materiality, and updating a Bayesian risk score the moment
            a signal lands.
          </p>

          <dl className="mt-8 grid grid-cols-3 gap-4">
            {[
              { value: '95.0%', label: 'Material events caught' },
              { value: '2 hrs', label: 'Median detection' },
              { value: '−49%', label: 'Analyst touches' },
            ].map((stat) => (
              <div key={stat.label}>
                <dt className="text-[20px] font-semibold tracking-[-0.02em]">{stat.value}</dt>
                <dd
                  className="mt-0.5 text-[11px] leading-snug"
                  style={{ color: 'var(--sidebar-muted)' }}
                >
                  {stat.label}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 text-[11px]" style={{ color: 'var(--sidebar-muted)' }}>
            Measured on a labeled 5,000-event scenario. Full method and numbers
            in the Evaluation view.
          </p>
        </div>

        <p className="text-[11px]" style={{ color: 'var(--sidebar-muted)' }}>
          Prototype built on synthetic data. Not connected to any production
          customer record.
        </p>
      </div>

      {/* Right: the form */}
      <div className="flex items-center justify-center bg-page p-6">
        <div className="w-full max-w-sm">
          <div className="lg:hidden">
            <div className="mb-6 flex items-center gap-2.5">
              <div
                className="grid size-9 place-items-center rounded-lg"
                style={{ background: 'var(--series-1)' }}
              >
                <Activity className="size-4.5 text-white" strokeWidth={2.5} />
              </div>
              <p className="text-[15px] font-semibold">Risk Trigger Engine</p>
            </div>
          </div>

          <h2 className="text-[20px] font-semibold tracking-[-0.02em] text-primary">
            Sign in
          </h2>
          <p className="mt-1 text-[13px] text-muted">
            Your role determines what you can view and what you can act on.
          </p>

          <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
            <Field label="Username">
              <TextInput
                value={username}
                onChange={setUsername}
                placeholder="a.chen"
                autoComplete="username"
                autoFocus
              />
            </Field>
            <Field label="Password">
              <TextInput
                value={password}
                onChange={setPassword}
                type="password"
                placeholder="••••••••"
                autoComplete="current-password"
              />
            </Field>

            {error && <Callout tone="critical">{error}</Callout>}

            <Button
              type="submit"
              variant="primary"
              loading={busy}
              icon={<ArrowRight className="size-3.5" />}
              className="mt-1 w-full"
              disabled={!username || !password}
            >
              Sign in
            </Button>
          </form>

          <div className="mt-8">
            <div className="mb-2.5 flex items-center gap-2">
              <Lock className="size-3 text-muted" />
              <p className="text-[11px] font-semibold tracking-wider text-muted uppercase">
                Demo accounts
              </p>
            </div>
            <div className="flex flex-col gap-1.5">
              {(roles.data?.demo_accounts ?? FALLBACK_ACCOUNTS).map((account) => (
                <button
                  key={account.username}
                  type="button"
                  onClick={() => fill(account.username)}
                  className={cx(
                    'flex items-center justify-between gap-3 rounded-lg border border-line bg-surface px-3 py-2 text-left transition-colors',
                    'hover:border-line-strong hover:bg-[var(--surface-hover)]',
                  )}
                >
                  <div className="min-w-0">
                    <p className="truncate text-[12.5px] font-medium text-primary">
                      {account.full_name}
                    </p>
                    <p className="truncate text-[11px] text-muted">
                      {ROLE_BLURB[account.role]}
                    </p>
                  </div>
                  <span
                    className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold tracking-wide"
                    style={{
                      background: `var(--wash-${
                        account.role === 'MANAGER'
                          ? 'good'
                          : account.role === 'AUDITOR'
                            ? 'warning'
                            : 'accent'
                      })`,
                      color: `var(--ink-${
                        account.role === 'MANAGER'
                          ? 'good'
                          : account.role === 'AUDITOR'
                            ? 'warning'
                            : 'accent'
                      })`,
                    }}
                  >
                    {account.role}
                  </span>
                </button>
              ))}
            </div>
            <p className="mt-2.5 text-[11px] leading-relaxed text-muted">
              Click an account to fill the form. Local demo credentials only —
              override with DEMO_ANALYST_PASSWORD and friends.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

// Matches the documented defaults in app/auth.py DEMO_USERS.
const DEMO_PASSWORDS: Record<string, string> = {
  'a.chen': 'analyst123',
  'r.okafor': 'manager123',
  'j.lindqvist': 'auditor123',
}

const FALLBACK_ACCOUNTS = [
  { username: 'a.chen', full_name: 'Amara Chen', role: 'ANALYST' as const },
  { username: 'r.okafor', full_name: 'Rem Okafor', role: 'MANAGER' as const },
  { username: 'j.lindqvist', full_name: 'Jo Lindqvist', role: 'AUDITOR' as const },
]
