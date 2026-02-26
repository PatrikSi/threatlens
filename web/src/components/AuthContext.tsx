import { createContext, useContext, useMemo, useState } from 'react'

import { getToken, setToken } from '../api/client'

interface AuthContextValue {
  token: string | null
  setAuthToken: (token: string | null) => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string | null>(getToken())

  const value = useMemo(
    () => ({
      token,
      setAuthToken: (nextToken: string | null) => {
        setTokenState(nextToken)
        setToken(nextToken)
      },
    }),
    [token],
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
