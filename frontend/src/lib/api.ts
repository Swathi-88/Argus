import type {
  Alert,
  AlertQueue,
  AlertStats,
  AuditTrail,
  ChainVerification,
  Customer,
  Disposition,
  EvaluationRun,
  ImmutabilityProof,
  LoginResponse,
  PipelineTrace,
  Principal,
  RiskExplanation,
  RiskTimeline,
  RoleMatrix,
} from './types'

// Vite proxies /api to the backend in dev (see vite.config.ts), so the browser
// stays same-origin. Overridable for a deployment that serves them separately.
const BASE = import.meta.env.VITE_API_BASE ?? '/api'

const TOKEN_KEY = 'rtc.token'
const USER_KEY = 'rtc.user'

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  },
  getUser: (): Principal | null => {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) return null
    try {
      return JSON.parse(raw) as Principal
    } catch {
      return null
    }
  },
  setUser: (user: Principal) => localStorage.setItem(USER_KEY, JSON.stringify(user)),
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

/** Fired on a 401 so the app can drop to the sign-in screen from anywhere. */
export const AUTH_EXPIRED_EVENT = 'rtc:auth-expired'

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = tokenStore.get()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${BASE}${path}`, { ...init, headers })

  if (response.status === 401) {
    tokenStore.clear()
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw new ApiError(401, 'Your session has expired. Please sign in again.')
  }

  if (!response.ok) {
    // FastAPI returns {detail: ...}; detail can be a string or a validation array.
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail)) {
        message = body.detail.map((d: any) => d.msg ?? JSON.stringify(d)).join('; ')
      }
    } catch {
      /* non-JSON error body — keep the status message */
    }
    throw new ApiError(response.status, message)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** Triggers a browser download for an endpoint that returns a file. */
async function download(path: string, fallbackName: string): Promise<void> {
  const token = tokenStore.get()
  const response = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })

  if (!response.ok) {
    let message = `Export failed (${response.status})`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      /* keep the status message */
    }
    throw new ApiError(response.status, message)
  }

  // Prefer the filename the API chose, so exports are self-describing.
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const matched = /filename="?([^"]+)"?/.exec(disposition)
  const filename = matched?.[1] ?? fallbackName

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

const query = (params: Record<string, string | number | boolean | null | undefined>) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const api = {
  // ------------------------------------------------------------------ auth
  login: (username: string, password: string) =>
    request<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<Principal>('/auth/me'),
  roles: () => request<RoleMatrix>('/auth/roles'),

  // ---------------------------------------------------------------- alerts
  alertQueue: (params: {
    status?: string
    tier?: string
    search?: string
    page?: number
    size?: number
  }) => request<AlertQueue>(`/alerts/queue${query(params)}`),
  alert: (id: number) => request<Alert>(`/alerts/${id}`),
  alertStats: () => request<AlertStats>('/alerts/stats/summary'),
  actOnAlert: (id: number, action: Disposition, notes?: string) =>
    request<Alert>(`/alerts/${id}/action`, {
      method: 'POST',
      body: JSON.stringify({ action, notes: notes || null }),
    }),

  // ------------------------------------------------------------- customers
  customer: (id: number) => request<Customer>(`/customers/${id}`),
  riskTimeline: (id: number) => request<RiskTimeline>(`/customers/${id}/risk-timeline`),
  riskExplanation: (id: number) => request<RiskExplanation>(`/customers/${id}/risk-explanation`),

  // ----------------------------------------------------------------- audit
  auditTrail: (params: {
    customer_id?: number
    origin?: string
    action?: string
    page?: number
    size?: number
  }) => request<AuditTrail>(`/audit${query(params)}`),
  verifyChain: (customerId?: number) =>
    request<ChainVerification>(`/audit/verify-chain${query({ customer_id: customerId })}`),
  immutabilityProof: () => request<ImmutabilityProof>('/audit/immutability-proof'),
  exportAudit: (format: 'json' | 'csv', customerId?: number, origin?: string) =>
    download(
      `/audit/export${query({ format, customer_id: customerId, origin })}`,
      `audit-trail.${format}`,
    ),

  // -------------------------------------------------------------- pipeline
  injectEvent: (payload: {
    entity_name: string
    event_type: string
    source: string
    category?: string | null
    severity?: string | null
    raw_payload?: Record<string, unknown> | null
  }) =>
    request<PipelineTrace>('/events/inject', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // ------------------------------------------------------------ evaluation
  latestEvaluation: () => request<EvaluationRun>('/evaluation/latest'),
  evaluationRuns: () => request<EvaluationRun[]>('/evaluation/runs'),
  runEvaluation: (payload: {
    num_customers: number
    num_events: number
    horizon_days: number
    baseline_review_interval_days: number
    random_seed: number
    fuzzy_match_threshold?: number | null
  }) =>
    request<EvaluationRun>('/evaluation/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
