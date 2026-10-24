import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { getToken, setToken, UNAUTHORIZED_EVENT } from './api/client'
import { useMe } from './api/hooks'
import type { BusinessBrief } from './api/types'

const BUSINESS_KEY = 'kassa.business'

interface AuthValue {
  token: string | null
  signIn: (token: string) => void
  signOut: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => getToken())
  const client = useQueryClient()

  const signIn = useCallback(
    (next: string) => {
      setToken(next)
      setTokenState(next)
      client.clear()
    },
    [client],
  )
  const signOut = useCallback(() => {
    setToken(null)
    setTokenState(null)
    client.clear()
  }, [client])

  useEffect(() => {
    const onUnauthorized = () => {
      setTokenState(null)
      client.clear()
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [client])

  const value = useMemo(() => ({ token, signIn, signOut }), [token, signIn, signOut])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth вне AuthProvider')
  return value
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  const location = useLocation()
  if (!token) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <>{children}</>
}

function readStoredBusiness(): string | null {
  try {
    return localStorage.getItem(BUSINESS_KEY)
  } catch {
    return null
  }
}

/** Текущий бизнес пользователя: выбранный ранее или первый. */
export function useCurrentBusiness() {
  const me = useMe()
  const [stored, setStored] = useState<string | null>(() => readStoredBusiness())
  const businesses: BusinessBrief[] = me.data?.businesses ?? []
  const business = businesses.find((b) => b.id === stored) ?? businesses[0] ?? null

  const select = useCallback((id: string) => {
    setStored(id)
    try {
      localStorage.setItem(BUSINESS_KEY, id)
    } catch {
      /* без сохранения */
    }
  }, [])

  return { me, user: me.data?.user ?? null, businesses, business, select }
}
