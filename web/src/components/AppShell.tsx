import { useEffect } from 'react'
import { Link, Outlet, useNavigate } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'

export function AppShell() {
  const { setAuthToken } = useAuth()
  const { mode, toggleMode } = useTheme()
  const meQuery = useCurrentUser()
  const navigate = useNavigate()

  const role = meQuery.data?.role

  useEffect(() => {
    if (meQuery.isError) {
      setAuthToken(null)
      navigate('/login')
    }
  }, [meQuery.isError, navigate, setAuthToken])

  return (
    <div className="min-h-screen text-ink dark:text-slate-100">
      <header className="border-b border-slate/20 bg-white/70 backdrop-blur dark:border-slate-700 dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-6">
            <h1 className="font-display text-2xl font-bold">ThreatLens</h1>
            <nav className="flex flex-wrap gap-2 text-sm font-semibold text-slate dark:text-slate-200">
              <Link to="/" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Dashboard
              </Link>
              <Link to="/feeds" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Feeds
              </Link>
              <Link to="/tokens" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                API Tokens
              </Link>
              <Link to="/account" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Account
              </Link>
              {role === 'admin' && (
                <>
                  <Link to="/users" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                    Users
                  </Link>
                  <Link to="/audit-logs" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                    Audit Logs
                  </Link>
                </>
              )}
            </nav>
          </div>

          <div className="flex items-center gap-2">
            {meQuery.data && (
              <div className="rounded border border-slate/20 px-2 py-1 text-xs text-slate dark:border-slate-700 dark:text-slate-300">
                {meQuery.data.email} ({meQuery.data.role})
              </div>
            )}
            <button
              className="rounded border border-slate/30 px-3 py-1 text-sm hover:border-cyan hover:text-cyan dark:border-slate-600"
              onClick={toggleMode}
            >
              {mode === 'dark' ? 'Light' : 'Dark'}
            </button>
            <button
              className="rounded border border-slate/30 px-3 py-1 text-sm hover:border-ember hover:text-ember dark:border-slate-600"
              onClick={() => {
                setAuthToken(null)
                navigate('/login')
              }}
            >
              Logout
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-5">
        <Outlet />
      </main>
    </div>
  )
}
