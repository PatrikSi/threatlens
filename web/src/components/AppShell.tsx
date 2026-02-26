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
      <header className="border-b border-slate/20 bg-white/70 backdrop-blur dark:border-cyan-900/40 dark:bg-[#030711]/92">
        <div className="flex w-full flex-wrap items-center justify-between gap-3 px-3 py-3 sm:px-4 lg:px-6">
          <div className="flex items-center gap-6">
            <h1 className="font-display text-2xl font-bold">ThreatLens</h1>
            <nav className="flex flex-wrap gap-2 text-sm font-semibold text-slate dark:text-cyan-100">
              <Link to="/" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Dashboard
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
            <button
              className="rounded border border-slate/30 px-3 py-1 text-sm hover:border-cyan hover:text-cyan dark:border-cyan-900/40 dark:bg-[#07101f]"
              onClick={toggleMode}
            >
              {mode === 'dark' ? 'Light' : 'Dark'}
            </button>
            <button
              className="rounded border border-slate/30 px-3 py-1 text-sm hover:border-ember hover:text-ember dark:border-cyan-900/40 dark:bg-[#07101f]"
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
      <main className="w-full px-3 py-4 sm:px-4 lg:px-6">
        <Outlet />
      </main>
    </div>
  )
}
