import { Link, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from './AuthContext'

export function AppShell() {
  const { setAuthToken } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="min-h-screen text-ink">
      <header className="border-b border-slate/20 bg-white/70 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <h1 className="font-display text-2xl font-bold">ThreatLens</h1>
            <nav className="flex gap-3 text-sm font-semibold text-slate">
              <Link to="/" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Dashboard
              </Link>
              <Link to="/feeds" className="rounded px-3 py-1 hover:bg-cyan/10 hover:text-cyan">
                Feeds
              </Link>
            </nav>
          </div>
          <button
            className="rounded border border-slate/30 px-3 py-1 text-sm hover:border-ember hover:text-ember"
            onClick={() => {
              setAuthToken(null)
              navigate('/login')
            }}
          >
            Logout
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-5">
        <Outlet />
      </main>
    </div>
  )
}
