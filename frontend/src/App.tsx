import { Navigate, Route, Routes } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { Spinner, ToastProvider } from './components/ui'
import { AuthProvider, useAuth } from './lib/auth'
import { AlertQueue } from './pages/AlertQueue'
import { AuditTrail } from './pages/AuditTrail'
import { Evaluation } from './pages/Evaluation'
import { Investigation } from './pages/Investigation'
import { LivePipeline } from './pages/LivePipeline'
import { SignIn } from './pages/SignIn'

function Routed() {
  const { user, ready, can } = useAuth()

  if (!ready) {
    return (
      <div className="grid h-full place-items-center">
        <Spinner label="Restoring session…" />
      </div>
    )
  }

  if (!user) return <SignIn />

  // The landing route depends on the role: an Auditor has no queue to work, so
  // dropping them on the audit trail is the useful default.
  const home = can('alerts:act') ? '/alerts' : can('audit:view') ? '/audit' : '/evaluation'

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to={home} replace />} />
        <Route path="/alerts" element={<AlertQueue />} />
        <Route path="/customers/:customerId" element={<Investigation />} />
        <Route path="/customers/:customerId/alerts/:alertId" element={<Investigation />} />
        <Route path="/audit" element={<AuditTrail />} />
        <Route path="/audit/:customerId" element={<AuditTrail />} />
        <Route path="/pipeline" element={<LivePipeline />} />
        <Route path="/evaluation" element={<Evaluation />} />
        <Route path="*" element={<Navigate to={home} replace />} />
      </Routes>
    </AppShell>
  )
}

export function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Routed />
      </ToastProvider>
    </AuthProvider>
  )
}
