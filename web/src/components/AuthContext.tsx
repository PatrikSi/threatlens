import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'

import { resetPendingReportingKeys } from '../pages/reportingRequestCoordinator'

interface AuthContextValue {
  sessionVersion: number
  markAuthenticated: () => void
  markLoggedOut: () => void
  observeAuthenticatedIdentity: (identity: string) => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)
const authSyncStorageKey = 'threatlens.auth.sync'
const authSyncChannelName = 'threatlens.auth'
const authSyncHandledStorageKey = 'threatlens.auth.last-handled'
const authIdentityStorageKey = 'threatlens.auth.identity'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [sessionVersion, setSessionVersion] = useState(0)
  const authChannelRef = useRef<BroadcastChannel | null>(null)
  const handledAuthEvent = useRef(
    readSessionStorageItem(authSyncHandledStorageKey),
  ).current
  const retainedAuthEvent = useRef(
    readLocalStorageItem(authSyncStorageKey),
  ).current
  const retainedAuthEventId = authEventId(retainedAuthEvent)
  const seenRemoteEventsRef = useRef(new Set<string>(
    optionalEntry(handledAuthEvent ?? retainedAuthEventId),
  ))
  const observedIdentityRef = useRef(readLocalStorageItem(authIdentityStorageKey))

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const applyRemoteChange = (value: unknown) => {
      const eventId = authEventId(value)
      if (!eventId || seenRemoteEventsRef.current.has(eventId)) return
      seenRemoteEventsRef.current.add(eventId)
      writeSessionStorageItem(authSyncHandledStorageKey, eventId)
      observedIdentityRef.current = readLocalStorageItem(authIdentityStorageKey)
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
    const latestRetainedAuthEvent = readLocalStorageItem(authSyncStorageKey)
    const latestRetainedAuthEventId = authEventId(latestRetainedAuthEvent)
    if (handledAuthEvent && latestRetainedAuthEventId !== handledAuthEvent) {
      applyRemoteChange(latestRetainedAuthEvent)
    } else if (
      !handledAuthEvent
      && latestRetainedAuthEventId
      && latestRetainedAuthEventId !== retainedAuthEventId
    ) {
      applyRemoteChange(latestRetainedAuthEvent)
    } else if (!handledAuthEvent && latestRetainedAuthEventId) {
      writeSessionStorageItem(authSyncHandledStorageKey, latestRetainedAuthEventId)
    }
    return () => {
      window.removeEventListener('storage', onStorage)
      authChannelRef.current?.removeEventListener('message', onBroadcast)
      authChannelRef.current?.close()
      authChannelRef.current = null
    }
  }, [handledAuthEvent, retainedAuthEvent, retainedAuthEventId])

  const value = useMemo(
    () => ({
      sessionVersion,
      markAuthenticated: () => publishAuthStateChange(
        setSessionVersion,
        authChannelRef.current,
      ),
      markLoggedOut: () => {
        observedIdentityRef.current = null
        removeLocalStorageItem(authIdentityStorageKey)
        publishAuthStateChange(setSessionVersion, authChannelRef.current)
      },
      observeAuthenticatedIdentity: (identity: string) => {
        const normalizedIdentity = identity.trim()
        if (!normalizedIdentity || observedIdentityRef.current === normalizedIdentity) {
          return
        }
        const previousIdentity = observedIdentityRef.current
        observedIdentityRef.current = normalizedIdentity
        writeLocalStorageItem(authIdentityStorageKey, normalizedIdentity)
        if (previousIdentity !== null) {
          publishAuthStateChange(setSessionVersion, authChannelRef.current)
        }
      },
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
  writeSessionStorageItem(authSyncHandledStorageKey, event.id)
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

function readLocalStorageItem(key: string): string | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeLocalStorageItem(key: string, value: string): void {
  try {
    if (typeof window !== 'undefined') window.localStorage.setItem(key, value)
  } catch {
    // Identity observation still protects this tab when storage is unavailable.
  }
}

function removeLocalStorageItem(key: string): void {
  try {
    if (typeof window !== 'undefined') window.localStorage.removeItem(key)
  } catch {
    // Identity observation still protects this tab when storage is unavailable.
  }
}

function writeSessionStorageItem(key: string, value: string): void {
  try {
    if (typeof window !== 'undefined') window.sessionStorage.setItem(key, value)
  } catch {
    // The in-memory event set still deduplicates this page lifecycle.
  }
}

function readSessionStorageItem(key: string): string | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function optionalEntry(value: string | null): string[] {
  return value ? [value] : []
}
