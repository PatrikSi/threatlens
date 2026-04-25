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
  { to: '/settings', label: 'Settings' },
]

export function AppShell() {
  const { markLoggedOut } = useAuth()
  const { mode, setMode, isDark } = useTheme()
  const meQuery = useCurrentUser()
  const navigate = useNavigate()
  const location = useLocation()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [logoutNotice, setLogoutNotice] = useState<string | null>(null)

  const isDashboardRoute = location.pathname === '/'
  const navLinks = NAV_LINKS

  const logout = useMutation({
    mutationFn: async () => {
      try {
        await apiFetch('/auth/logout', { method: 'POST' })
        return { shouldClearLocalSession: true as const }
      } catch (error) {
        if (isServerExpiredLogout(error)) {
          return { shouldClearLocalSession: true as const }
        }
        throw error
      }
    },
    onMutate: () => {
      setLogoutNotice(null)
    },
    onSuccess: (result) => {
      if (result.shouldClearLocalSession) {
        markLoggedOut()
        navigate('/login', { replace: true })
      }
    },
    onError: (error) => {
      setLogoutNotice(resolveLogoutFailureNotice(error))
    },
  })

  useEffect(() => {
    if (!meQuery.error) {
      return
    }

    if (meQuery.error instanceof ApiError && meQuery.error.status === 401) {
      markLoggedOut()
      navigate('/login', { replace: true, state: { authMessage: 'Session expired. Sign in again.' } })
    }
  }, [markLoggedOut, meQuery.error, navigate])

  useEffect(() => {
    setMobileNavOpen(false)
  }, [location.pathname])

  return (
    <div className="min-h-screen text-ink dark:text-slate-100">
      <header className="border-b border-slate/20 bg-white/70 backdrop-blur dark:border-white/10 dark:bg-slate-950/70">
        <div className="px-3 py-3 sm:px-4 lg:hidden">
          <div className="flex items-center justify-between gap-3">
            <h1 className="font-display text-2xl font-bold">ThreatLens</h1>
            <button
              type="button"
              className="rounded border border-slate/20 px-3 py-1.5 text-sm text-slate-700 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-100"
              onClick={() => setMobileNavOpen((current) => !current)}
              aria-expanded={mobileNavOpen}
              aria-label="Toggle navigation menu"
            >
              {mobileNavOpen ? 'Close' : 'Menu'}
            </button>
          </div>

          {mobileNavOpen && (
            <div className="mt-3 space-y-3 border-t border-slate/20 pt-3 dark:border-white/10">
              <nav className="grid grid-cols-2 gap-2 text-sm font-semibold text-slate dark:text-slate-200">
                {navLinks.map((link) => {
                  const active = isNavLinkActive(location.pathname, link.to)
                  return (
                    <Link
                      key={link.to}
                      to={link.to}
                      aria-current={active ? 'page' : undefined}
                      className={`rounded border px-3 py-1.5 text-center transition ${
                        active
                          ? 'border-cyan/40 bg-cyan/10 font-bold text-cyan shadow-[inset_0_0_0_1px_rgba(8,145,178,0.12)] ring-1 ring-cyan/10 dark:border-cyan-500/40 dark:bg-cyan-500/15 dark:text-cyan-100'
                          : 'border-slate/20 hover:bg-cyan/10 hover:text-cyan dark:border-white/10 dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06] dark:hover:text-cyan-100'
                      }`}
                    >
                      {link.label}
                    </Link>
                  )
                })}
              </nav>

              {meQuery.data && (
                <div className="rounded border border-slate/20 px-2.5 py-1.5 text-sm text-slate dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-200">
                  {meQuery.data.email} ({meQuery.data.role})
                </div>
              )}

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="flex-1 rounded border border-slate/20 px-2.5 py-1.5 text-sm text-slate-700 transition hover:border-slate/30 hover:bg-white/80 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-100 dark:hover:bg-white/[0.08]"
                  onClick={() => setMode(isDark ? 'light' : 'dark')}
                >
                  {isDark ? 'Switch to light mode' : 'Switch to dark mode'}
                </button>
                <button
                  className="rounded border border-slate/20 px-3 py-1.5 text-sm text-slate-700 hover:border-ember hover:text-ember dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-100 dark:hover:border-red-800 dark:hover:text-red-300"
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
            <nav className="flex flex-wrap gap-2 text-sm font-semibold text-slate dark:text-slate-200">
              {navLinks.map((link) => {
                const active = isNavLinkActive(location.pathname, link.to)
                return (
                  <Link
                    key={link.to}
                    to={link.to}
                    aria-current={active ? 'page' : undefined}
                    className={`rounded border px-3 py-1.5 transition ${
                      active
                        ? 'border-cyan/40 bg-cyan/10 font-bold text-cyan shadow-[inset_0_0_0_1px_rgba(8,145,178,0.12)] ring-1 ring-cyan/10 dark:border-cyan-500/40 dark:bg-cyan-500/15 dark:text-cyan-100'
                        : 'border-transparent hover:border-slate/20 hover:bg-cyan/10 hover:text-cyan dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06] dark:hover:text-cyan-100'
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
              <div className="rounded border border-slate/20 px-2.5 py-1.5 text-sm text-slate dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-200">
                {meQuery.data.email} ({meQuery.data.role})
              </div>
            )}
            <button
              type="button"
              className="rounded border border-slate/20 px-2.5 py-1.5 text-sm text-slate-700 transition hover:border-slate/30 hover:bg-white/80 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-100 dark:hover:bg-white/[0.08]"
              onClick={() => setMode(isDark ? 'light' : 'dark')}
            >
              {mode === 'dark' ? 'Light mode' : 'Dark mode'}
            </button>
            <button
              className="rounded border border-slate/20 px-3 py-1.5 text-sm text-slate-700 hover:border-ember hover:text-ember dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-100 dark:hover:border-red-800 dark:hover:text-red-300"
              onClick={() => {
                logout.mutate()
              }}
              disabled={logout.isPending}
            >
              {logout.isPending ? 'Logging out...' : 'Logout'}
            </button>
          </div>
        </div>
        {logoutNotice && (
          <div role="alert" aria-live="assertive" aria-atomic="true" className="px-3 pb-3 text-sm text-amber-700 dark:px-6 dark:text-amber-300">
            {logoutNotice}
          </div>
        )}
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

function isServerExpiredLogout(error: unknown) {
  if (!(error instanceof ApiError)) {
    return false
  }
  if (error.status === 401) {
    return true
  }
  return error.status === 403 && error.message !== 'Missing or invalid CSRF token'
}

function resolveLogoutFailureNotice(error: unknown) {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return `Logout was not completed because the server returned an error: ${error.message}`
  }

  if (error instanceof Error && error.message.trim()) {
    return `Logout was not completed because the request failed: ${error.message}`
  }

  return 'Logout was not completed. Check your connection and try again.'
}
