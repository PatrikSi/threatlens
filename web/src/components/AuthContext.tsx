import { createContext, useContext, useMemo, useState } from 'react'

interface AuthContextValue {
  sessionVersion: number
  markAuthenticated: () => void
  markLoggedOut: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [sessionVersion, setSessionVersion] = useState(0)

  const value = useMemo(
    () => ({
      sessionVersion,
      markAuthenticated: () => setSessionVersion((current) => current + 1),
      markLoggedOut: () => setSessionVersion((current) => current + 1),
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
