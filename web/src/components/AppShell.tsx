import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'

const NAV_LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/feeds', label: 'Feeds' },
  { to: '/stats', label: 'Stats' },
  { to: '/ai', label: 'AI', adminOnly: true, requiresAi: true },
  { to: '/settings', label: 'Settings' },
]

export function AppShell() {
  const { markLoggedOut } = useAuth()
  const { mode, setMode, darkThemes } = useTheme()
  const meQuery = useCurrentUser()
  const navigate = useNavigate()
  const location = useLocation()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  const isDashboardRoute = location.pathname === '/'
  const navLinks = NAV_LINKS.filter((link) => {
    if (link.adminOnly && meQuery.data?.role !== 'admin') {
      return false
    }
    if (link.requiresAi && !meQuery.data?.features.ai_enabled) {
      return false
    }
    return true
  })

  const logout = useMutation({
    mutationFn: () => apiFetch('/auth/logout', { method: 'POST' }),
    onSettled: () => {
      markLoggedOut()
      navigate('/login', { replace: true })
    },
  })

  useEffect(() => {
    if (!meQuery.error) {
      return
    }

    if (meQuery.error instanceof ApiError && (meQuery.error.status === 401 || meQuery.error.status === 403)) {
      markLoggedOut()
      navigate('/login', { replace: true })
    }
  }, [markLoggedOut, meQuery.error, navigate])

  useEffect(() => {
    setMobileNavOpen(false)
  }, [location.pathname])

  return (
    <div className="min-h-screen text-ink dark:text-slate-100">
      <header className="border-b border-slate/20 bg-white/70 backdrop-blur dark:border-cyan-900/40 dark:bg-[#03140f]/92">
        <div className="px-3 py-3 sm:px-4 lg:hidden">
          <div className="flex items-center justify-between gap-3">
            <h1 className="font-display text-2xl font-bold">ThreatLens</h1>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-1.5 text-sm text-slate-700 dark:border-cyan-900/40 dark:bg-[#08211b] dark:text-cyan-100"
              onClick={() => setMobileNavOpen((current) => !current)}
              aria-expanded={mobileNavOpen}
              aria-label="Toggle navigation menu"
            >
              {mobileNavOpen ? 'Close' : 'Menu'}
            </button>
          </div>

          {mobileNavOpen && (
            <div className="mt-3 space-y-3 border-t border-slate/20 pt-3 dark:border-cyan-900/40">
              <nav className="grid grid-cols-2 gap-2 text-sm font-semibold text-slate dark:text-cyan-100">
                {navLinks.map((link) => {
                  const active = isNavLinkActive(location.pathname, link.to)
                  return (
                    <Link
                      key={link.to}
                      to={link.to}
                      aria-current={active ? 'page' : undefined}
                      className={`rounded border px-3 py-2 text-center transition ${
                        active
                          ? 'border-cyan/40 bg-cyan/10 font-bold text-cyan shadow-[inset_0_0_0_1px_rgba(8,145,178,0.12)] ring-1 ring-cyan/10 dark:border-cyan-700/45 dark:bg-cyan-950/45 dark:text-cyan-200 dark:ring-cyan-900/30'
                          : 'border-slate/20 hover:bg-cyan/10 hover:text-cyan dark:border-cyan-900/40'
                      }`}
                    >
                      {link.label}
                    </Link>
                  )
                })}
              </nav>

              {meQuery.data && (
                <div className="rounded border border-slate/20 px-2 py-1.5 text-xs text-slate dark:border-cyan-900/40 dark:bg-cyan-950/30 dark:text-cyan-200">
                  {meQuery.data.email} ({meQuery.data.role})
                </div>
              )}

              <div className="flex items-center gap-2">
                <label className="flex-1 rounded border border-slate/30 px-2 py-1.5 text-sm text-slate-700 dark:border-cyan-900/40 dark:bg-[#08211b] dark:text-cyan-100">
                  <span className="sr-only">Theme</span>
                  <select
                    value={mode}
                    onChange={(event) => setMode(event.target.value as typeof mode)}
                    className="w-full bg-transparent text-sm outline-none"
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
                  className="rounded border border-slate/30 px-3 py-1.5 text-sm text-slate-700 hover:border-ember hover:text-ember dark:border-cyan-900/40 dark:bg-[#08211b] dark:text-cyan-100"
                  onClick={() => {
                    logout.mutate()
                  }}
                  disabled={logout.isPending}
                >
                  {logout.isPending ? 'Logging out...' : 'Logout'}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="hidden w-full flex-wrap items-center justify-between gap-3 px-3 py-3 sm:px-4 lg:flex lg:px-6">
          <div className="flex items-center gap-6">
            <h1 className="font-display text-2xl font-bold">ThreatLens</h1>
            <nav className="flex flex-wrap gap-2 text-sm font-semibold text-slate dark:text-cyan-100">
              {navLinks.map((link) => {
                const active = isNavLinkActive(location.pathname, link.to)
                return (
                  <Link
                    key={link.to}
                    to={link.to}
                    aria-current={active ? 'page' : undefined}
                    className={`rounded-lg border px-3 py-1.5 transition ${
                      active
                        ? 'border-cyan/40 bg-cyan/10 font-bold text-cyan shadow-[inset_0_0_0_1px_rgba(8,145,178,0.12)] ring-1 ring-cyan/10 dark:border-cyan-700/45 dark:bg-cyan-950/45 dark:text-cyan-200 dark:ring-cyan-900/30'
                        : 'border-transparent hover:border-slate/20 hover:bg-cyan/10 hover:text-cyan dark:hover:border-cyan-900/40'
                    }`}
                  >
                    {link.label}
                  </Link>
                )
              })}
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
                logout.mutate()
              }}
              disabled={logout.isPending}
            >
              {logout.isPending ? 'Logging out...' : 'Logout'}
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

function isNavLinkActive(pathname: string, targetPath: string) {
  if (targetPath === '/') {
    return pathname === '/'
  }
  return pathname === targetPath || pathname.startsWith(`${targetPath}/`)
}
