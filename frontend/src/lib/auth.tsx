import type { ReactNode } from 'react'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { AUTH_EXPIRED_EVENT, api, tokenStore } from './api'
import type { Principal } from './types'

/** Permission verbs, mirroring app/auth.py. */
export const PERM = {
  viewAlerts: 'alerts:view',
  actAlerts: 'alerts:act',
  escalateAlerts: 'alerts:escalate',
  viewCustomers: 'customers:view',
  ingestEvents: 'events:ingest',
  viewAudit: 'audit:view',
  exportAudit: 'audit:export',
  verifyAudit: 'audit:verify',
  viewEvaluation: 'evaluation:view',
  runEvaluation: 'evaluation:run',
} as const

interface AuthState {
  user: Principal | null
  ready: boolean
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => void
  can: (permission: string) => boolean
}

const AuthContext = createContext<AuthState>({
  user: null,
  ready: false,
  signIn: async () => {},
  signOut: () => {},
  can: () => false,
})

export const useAuth = () => useContext(AuthContext)

export function AuthProvider({ children }: { children: ReactNode }) {
  // Seeded from storage so a reload does not flash the sign-in screen before
  // the /auth/me round trip confirms the token.
  const [user, setUser] = useState<Principal | null>(() => tokenStore.getUser())
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let cancelled = false

    const confirm = async () => {
      if (!tokenStore.get()) {
        setReady(true)
        return
      }
      try {
        const me = await api.me()
        if (!cancelled) {
          setUser(me)
          tokenStore.setUser(me)
        }
      } catch {
        // Expired or tampered token — drop it and show sign-in.
        if (!cancelled) {
          tokenStore.clear()
          setUser(null)
        }
      } finally {
        if (!cancelled) setReady(true)
      }
    }

    void confirm()
    return () => {
      cancelled = true
    }
  }, [])

  // A 401 from any request anywhere unwinds the session.
  useEffect(() => {
    const onExpired = () => setUser(null)
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [])

  const signIn = useCallback(async (username: string, password: string) => {
    const response = await api.login(username, password)
    tokenStore.set(response.access_token)
    tokenStore.setUser(response.user)
    setUser(response.user)
  }, [])

  const signOut = useCallback(() => {
    tokenStore.clear()
    setUser(null)
  }, [])

  const can = useCallback(
    (permission: string) => Boolean(user?.permissions.includes(permission)),
    [user],
  )

  const value = useMemo<AuthState>(
    () => ({ user, ready, signIn, signOut, can }),
    [user, ready, signIn, signOut, can],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
