import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'

import { resetPendingReportingKeys } from '../pages/reportingRequestCoordinator'

interface AuthContextValue {
  sessionVersion: number
  markAuthenticated: () => void
  markLoggedOut: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)
const authSyncStorageKey = 'threatlens.auth.sync'
const authSyncChannelName = 'threatlens.auth'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [sessionVersion, setSessionVersion] = useState(0)
  const authChannelRef = useRef<BroadcastChannel | null>(null)
  const seenRemoteEventsRef = useRef(new Set<string>())

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const applyRemoteChange = (value: unknown) => {
      const eventId = authEventId(value)
      if (!eventId || seenRemoteEventsRef.current.has(eventId)) return
      seenRemoteEventsRef.current.add(eventId)
      if (seenRemoteEventsRef.current.size > 32) {
        const oldest = seenRemoteEventsRef.current.values().next().value
        if (oldest) seenRemoteEventsRef.current.delete(oldest)
      }
      resetPendingReportingKeys()
      setSessionVersion((current) => current + 1)
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key !== authSyncStorageKey || !event.newValue) {
        return
      }
      applyRemoteChange(event.newValue)
    }
    const onBroadcast = (event: MessageEvent<unknown>) => {
      applyRemoteChange(event.data)
    }

    try {
      authChannelRef.current = new BroadcastChannel(authSyncChannelName)
      authChannelRef.current.addEventListener('message', onBroadcast)
    } catch {
      authChannelRef.current = null
    }

    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener('storage', onStorage)
      authChannelRef.current?.removeEventListener('message', onBroadcast)
      authChannelRef.current?.close()
      authChannelRef.current = null
    }
  }, [])

  const value = useMemo(
    () => ({
      sessionVersion,
      markAuthenticated: () => publishAuthStateChange(
        setSessionVersion,
        authChannelRef.current,
      ),
      markLoggedOut: () => publishAuthStateChange(
        setSessionVersion,
        authChannelRef.current,
      ),
    }),
    [sessionVersion],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}

function publishAuthStateChange(
  setSessionVersion: React.Dispatch<React.SetStateAction<number>>,
  authChannel: BroadcastChannel | null,
) {
  resetPendingReportingKeys()
  setSessionVersion((current) => current + 1)
  const event = {
    id: createAuthEventId(),
    at: Date.now(),
  }
  try {
    authChannel?.postMessage(event)
  } catch {
    // localStorage remains available as a second cross-tab signal.
  }

  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(
      authSyncStorageKey,
      JSON.stringify(event),
    )
  } catch {
    // No-op when storage is unavailable.
  }
}

function authEventId(value: unknown): string | null {
  try {
    const parsed = typeof value === 'string' ? JSON.parse(value) : value
    if (!parsed || typeof parsed !== 'object') return null
    const event = parsed as { id?: unknown; nonce?: unknown; at?: unknown }
    if (typeof event.id === 'string' && event.id) return event.id
    if (typeof event.nonce === 'string' && event.nonce) {
      return `legacy:${event.nonce}`
    }
    return typeof event.at === 'number' && Number.isFinite(event.at)
      ? `legacy-at:${event.at}`
      : null
  } catch {
    return null
  }
}

function createAuthEventId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}
