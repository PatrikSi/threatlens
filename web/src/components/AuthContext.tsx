import { createContext, useContext, useEffect, useMemo, useState } from 'react'

interface AuthContextValue {
  sessionVersion: number
  markAuthenticated: () => void
  markLoggedOut: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)
const authSyncStorageKey = 'threatlens.auth.sync'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [sessionVersion, setSessionVersion] = useState(0)

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const onStorage = (event: StorageEvent) => {
      if (event.key !== authSyncStorageKey || !event.newValue) {
        return
      }
      setSessionVersion((current) => current + 1)
    }

    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const value = useMemo(
    () => ({
      sessionVersion,
      markAuthenticated: () => publishAuthStateChange(setSessionVersion),
      markLoggedOut: () => publishAuthStateChange(setSessionVersion),
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

function publishAuthStateChange(setSessionVersion: React.Dispatch<React.SetStateAction<number>>) {
  setSessionVersion((current) => current + 1)

  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(
      authSyncStorageKey,
      JSON.stringify({ at: Date.now(), nonce: Math.random().toString(36).slice(2) }),
    )
  } catch {
    // No-op when storage is unavailable.
  }
}
