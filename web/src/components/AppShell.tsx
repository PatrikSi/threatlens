import { useEffect } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'

export function AppShell() {
  const { setAuthToken } = useAuth()
  const { mode, setMode, darkThemes } = useTheme()
  const meQuery = useCurrentUser()
  const navigate = useNavigate()
  const location = useLocation()

  const role = meQuery.data?.role
  const isDashboardRoute = location.pathname === '/'

  useEffect(() => {
    if (!meQuery.error) {
      return
    }

    if (meQuery.error instanceof ApiError && (meQuery.error.status === 401 || meQuery.error.status === 403)) {
      setAuthToken(null)
      navigate('/login', { replace: true })
    }
  }, [meQuery.error, navigate, setAuthToken])

  return (
    <div className="min-h-screen text-ink dark:text-slate-100">
      <header className="border-b border-slate/20 bg-white/70 backdrop-blur dark:border-cyan-900/40 dark:bg-[#03140f]/92">
        <div className="flex w-full flex-wrap items-center justify-between gap-3 px-3 py-3 sm:px-4 lg:px-6">
          <div className="flex items-center gap-6">
            <h1 className="font-display text-2xl font-bold">ThreatLens</h1>
            <nav className="flex flex-wrap gap-2 text-sm font-semibold text-slate dark:text-cyan-100">
              <Link to="/" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Dashboard
              </Link>
              <Link to="/alerts" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Alerts
              </Link>
              <Link to="/feeds" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Feeds
              </Link>
              <Link to="/stats" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Stats
              </Link>
              <Link to="/settings" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Settings
              </Link>
            </nav>
          </div>

          <div className="flex items-center gap-2">
            {meQuery.data && (
              <div className="rounded border border-slate/20 px-2 py-1 text-xs text-slate dark:border-cyan-900/40 dark:bg-cyan-950/30 dark:text-cyan-200">
                {meQuery.data.email} ({meQuery.data.role})
              </div>
            )}
            <label className="rounded border border-slate/30 px-2 py-1 text-sm text-slate-700 dark:border-cyan-900/40 dark:bg-[#08211b] dark:text-cyan-100">
              <span className="sr-only">Theme</span>
              <select
                value={mode}
                onChange={(event) => setMode(event.target.value as typeof mode)}
                className="bg-transparent text-sm outline-none"
              >
                <option value="light">Light</option>
                {darkThemes.map((theme) => (
                  <option key={theme.id} value={theme.id}>
                    {theme.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="rounded border border-slate/30 px-3 py-1 text-sm text-slate-700 hover:border-ember hover:text-ember dark:border-cyan-900/40 dark:bg-[#08211b] dark:text-cyan-100"
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
      <main className={`w-full ${isDashboardRoute ? 'px-0 py-0' : 'px-3 py-4 sm:px-4 lg:px-6'}`}>
        <Outlet />
      </main>
    </div>
  )
}
