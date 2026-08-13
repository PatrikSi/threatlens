import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'

const NAV_LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/feeds', label: 'Feeds' },
  { to: '/stats', label: 'Stats' },
  { to: '/export', label: 'Export' },
  { to: '/settings', label: 'Settings' },
]
const APP_VERSION = import.meta.env.VITE_APP_VERSION || '1.0.0'

export function AppShell() {
  const { markLoggedOut } = useAuth()
  const { setMode, isDark } = useTheme()
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
    <div className="flex min-h-screen flex-col text-ink dark:text-slate-100">
      <header className="tl-app-header">
        <div className="px-3 py-2 sm:px-4 sm:py-3 lg:hidden">
          <div className="flex items-center justify-between gap-3">
            <h1 className="font-display text-xl font-bold">ThreatLens</h1>
            <button
              type="button"
              className="tl-subtle-control rounded px-3 py-1.5 text-sm font-semibold"
              onClick={() => setMobileNavOpen((current) => !current)}
              aria-expanded={mobileNavOpen}
              aria-controls="mobile-primary-navigation"
              aria-label="Toggle navigation menu"
            >
              {mobileNavOpen ? 'Close' : 'Menu'}
            </button>
          </div>

          {mobileNavOpen && (
            <div id="mobile-primary-navigation" className="mt-3 border-t border-slate/20 pt-2 dark:border-white/10">
              <nav className="divide-y divide-slate/15 text-sm font-semibold text-slate dark:divide-white/10 dark:text-slate-200">
                {navLinks.map((link) => {
                  const active = isNavLinkActive(location.pathname, link.to)
                  return (
                    <Link
                      key={link.to}
                      to={link.to}
                      aria-current={active ? 'page' : undefined}
                      className={`block border-l-2 px-3 py-3 text-left transition ${
                        active
                          ? 'border-l-cyan bg-cyan/10 font-bold text-cyan dark:bg-cyan/10 dark:text-cyan-100'
                          : 'border-l-transparent hover:bg-cyan/10 hover:text-cyan dark:hover:bg-white/[0.06] dark:hover:text-cyan-100'
                      }`}
                    >
                      {link.label}
                    </Link>
                  )
                })}
              </nav>

              <div className="mt-3 space-y-2 border-t border-slate/20 pt-3 dark:border-white/10">
                {meQuery.data && (
                  <div className="min-w-0 px-1 text-sm text-slate dark:text-slate-200">
                    <p className="break-all font-semibold text-ink dark:text-slate-100">{meQuery.data.email}</p>
                    <p className="mt-0.5 text-xs capitalize text-slate dark:text-slate-400">{meQuery.data.role}</p>
                  </div>
                )}
                <button
                  type="button"
                  className="tl-subtle-control w-full rounded px-3 py-2 text-left text-sm transition"
                  onClick={() => setMode(isDark ? 'light' : 'dark')}
                  aria-pressed={isDark}
                  aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
                >
                  Appearance: {isDark ? 'Dark mode' : 'Light mode'}
                </button>
                <button
                  className="w-full rounded border border-slate/20 px-3 py-2 text-left text-sm text-slate-700 hover:border-ember hover:text-ember dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-100 dark:hover:border-red-800 dark:hover:text-red-300"
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
                        ? 'tl-nav-link-active font-bold'
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
              <div className="tl-subtle-control rounded px-2.5 py-1.5 text-sm">
                {meQuery.data.email} ({meQuery.data.role})
              </div>
            )}
            <button
              type="button"
              className="tl-subtle-control rounded px-2.5 py-1.5 text-sm transition"
              onClick={() => setMode(isDark ? 'light' : 'dark')}
              aria-pressed={isDark}
              aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {isDark ? 'Dark mode' : 'Light mode'}
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
      <main className={`tl-app-content w-full flex-1 ${isDashboardRoute ? 'px-0 py-0' : 'px-2 py-2 sm:px-4 sm:py-4 lg:px-6'}`}>
        <Outlet />
      </main>
      <footer className="px-3 py-3 text-right text-[11px] text-slate/55 dark:text-slate-400/60 sm:px-4 lg:px-6">
        <span aria-label={`ThreatLens version ${APP_VERSION}`}>v{APP_VERSION}</span>
      </footer>
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
  return resolveApiErrorMessage(error, 'Logout could not be completed', { includeTechnicalDetail: false })
}
