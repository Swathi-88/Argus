import {
  Activity,
  BarChart3,
  FileClock,
  Inbox,
  LogOut,
  Moon,
  Radio,
  ShieldCheck,
  Sun,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

import { PERM, useAuth } from '../lib/auth'
import { useTheme } from '../lib/useTheme'
import { cx } from './ui'

const NAV = [
  {
    to: '/alerts',
    label: 'Alert queue',
    icon: Inbox,
    permission: PERM.viewAlerts,
    hint: 'Live reassessment alerts, highest priority first',
  },
  {
    to: '/pipeline',
    label: 'Live pipeline',
    icon: Radio,
    permission: PERM.ingestEvents,
    hint: 'Inject an event and watch it flow through',
  },
  {
    to: '/audit',
    label: 'Audit trail',
    icon: FileClock,
    permission: PERM.viewAudit,
    hint: 'Append-only record of every action',
  },
  {
    to: '/evaluation',
    label: 'Evaluation',
    icon: BarChart3,
    permission: PERM.viewEvaluation,
    hint: 'Baseline vs engine comparison',
  },
]

const ROLE_TONE: Record<string, string> = {
  ANALYST: 'var(--ink-accent)',
  MANAGER: 'var(--ink-good)',
  AUDITOR: 'var(--ink-warning)',
}

const ROLE_WASH: Record<string, string> = {
  ANALYST: 'var(--wash-accent)',
  MANAGER: 'var(--wash-good)',
  AUDITOR: 'var(--wash-warning)',
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, signOut, can } = useAuth()
  const { theme, toggle } = useTheme()
  const location = useLocation()

  const visible = NAV.filter((item) => can(item.permission))

  return (
    <div className="flex h-full">
      {/* Sidebar: dark in both themes. A compliance console is worked in for
          hours, and a persistent dark rail keeps the eye on the data panes. */}
      <aside
        className="hidden w-60 shrink-0 flex-col justify-between md:flex"
        style={{ background: 'var(--sidebar)', color: 'var(--sidebar-text)' }}
      >
        <div>
          <div className="flex items-center gap-2.5 px-5 pt-5 pb-6">
            <div
              className="grid size-8 shrink-0 place-items-center rounded-lg"
              style={{ background: 'var(--series-1)' }}
            >
              <Activity className="size-4 text-white" strokeWidth={2.5} />
            </div>
            <div className="min-w-0">
              <p className="truncate text-[13px] leading-tight font-semibold">
                Risk Trigger
              </p>
              <p
                className="truncate text-[11px] leading-tight"
                style={{ color: 'var(--sidebar-muted)' }}
              >
                Continuous KYC
              </p>
            </div>
          </div>

          <nav className="flex flex-col gap-0.5 px-2.5">
            {visible.map((item) => {
              const active =
                location.pathname === item.to || location.pathname.startsWith(`${item.to}/`)
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  title={item.hint}
                  className={cx(
                    'group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors',
                  )}
                  style={{
                    background: active ? 'var(--sidebar-active)' : undefined,
                    color: active ? 'var(--sidebar-text)' : 'var(--sidebar-muted)',
                  }}
                >
                  <item.icon className="size-4 shrink-0" />
                  <span className="truncate">{item.label}</span>
                </NavLink>
              )
            })}
          </nav>
        </div>

        <div className="px-2.5 pb-4">
          {/* The role is shown permanently, not buried in a menu: what the
              signed-in user is allowed to do explains the greyed-out controls. */}
          <div
            className="mb-2 rounded-lg px-2.5 py-2.5"
            style={{ background: 'var(--sidebar-hover)' }}
          >
            <div className="flex items-center gap-2">
              <div
                className="grid size-7 shrink-0 place-items-center rounded-full text-[11px] font-semibold"
                style={{
                  background: ROLE_WASH[user?.role ?? ''] ?? 'var(--wash-neutral)',
                  color: ROLE_TONE[user?.role ?? ''] ?? 'var(--text-secondary)',
                }}
              >
                {user?.full_name
                  .split(' ')
                  .map((part) => part[0])
                  .slice(0, 2)
                  .join('')}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[12px] leading-tight font-medium">
                  {user?.full_name}
                </p>
                <p
                  className="truncate text-[11px] leading-tight"
                  style={{ color: 'var(--sidebar-muted)' }}
                >
                  {user?.role}
                </p>
              </div>
            </div>
          </div>

          <div className="flex gap-1">
            <button
              onClick={toggle}
              title={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] transition-colors"
              style={{ color: 'var(--sidebar-muted)' }}
            >
              {theme === 'light' ? <Moon className="size-3.5" /> : <Sun className="size-3.5" />}
              {theme === 'light' ? 'Dark' : 'Light'}
            </button>
            <button
              onClick={signOut}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] transition-colors"
              style={{ color: 'var(--sidebar-muted)' }}
            >
              <LogOut className="size-3.5" />
              Sign out
            </button>
          </div>
        </div>
      </aside>

      {/* Mobile nav */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2.5 md:hidden">
          <div className="flex items-center gap-2">
            <div
              className="grid size-7 place-items-center rounded-lg"
              style={{ background: 'var(--series-1)' }}
            >
              <Activity className="size-3.5 text-white" strokeWidth={2.5} />
            </div>
            <span className="text-[13px] font-semibold">Risk Trigger</span>
          </div>
          <div className="flex items-center gap-1">
            {visible.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                aria-label={item.label}
                className="rounded-lg p-2 text-muted"
                style={({ isActive }: { isActive: boolean }) =>
                  isActive
                    ? { background: 'var(--wash-accent)', color: 'var(--ink-accent)' }
                    : undefined
                }
              >
                <item.icon className="size-4" />
              </NavLink>
            ))}
            <button onClick={toggle} className="rounded-lg p-2 text-muted">
              {theme === 'light' ? <Moon className="size-4" /> : <Sun className="size-4" />}
            </button>
            <button onClick={signOut} className="rounded-lg p-2 text-muted">
              <LogOut className="size-4" />
            </button>
          </div>
        </header>

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] px-5 py-6 md:px-8 md:py-8">{children}</div>
        </main>
      </div>
    </div>
  )
}

/** Small read-only marker for a control the current role cannot use. */
export function ReadOnlyNotice({ role }: { role: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-muted">
      <ShieldCheck className="size-3.5" />
      {role} is read-only on alerts
    </span>
  )
}
