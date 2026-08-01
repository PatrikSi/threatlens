import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'

export function SettingsLayout() {
  const meQuery = useCurrentUser()
  const location = useLocation()
  const [integrationsExpanded, setIntegrationsExpanded] = useState(false)
  const [mobileSettingsOpen, setMobileSettingsOpen] = useState(false)
  const role = meQuery.data?.role
  const aiEnabled = meQuery.data?.features.ai_enabled ?? false
  const integrationsActive = isSettingsLinkActive(location.pathname, '/settings/integrations')
  const showIntegrationsChildren = integrationsActive || integrationsExpanded
  const webhooksActive = isSettingsLinkActive(location.pathname, '/settings/integrations/webhooks')
  const smtpActive = isSettingsLinkActive(location.pathname, '/settings/integrations/smtp')

  const navItems = [
    { to: '/settings/account', label: 'Account' },
    { to: '/settings/tokens', label: 'API Tokens' },
    ...(role === 'admin' && aiEnabled ? [{ to: '/settings/ai', label: 'AI' }] : []),
    ...(role === 'admin' ? [{ to: '/settings/tagging', label: 'Tagging' }] : []),
    ...(role === 'admin' ? [{ to: '/settings/identity', label: 'Identity' }] : []),
    ...(role === 'admin' ? [{ to: '/settings/users', label: 'Users' }] : []),
    ...(role === 'admin' ? [{ to: '/settings/audit-logs', label: 'Audit Logs' }] : []),
  ]
  const activeSettingsLabel = smtpActive
    ? 'Integrations / SMTP'
    : webhooksActive
      ? 'Integrations / Webhooks'
      : navItems.find((item) => isSettingsLinkActive(location.pathname, item.to))?.label ?? 'Settings'

  useEffect(() => {
    setMobileSettingsOpen(false)
  }, [location.pathname])

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="tl-surface min-w-0 rounded-lg p-3 lg:hidden">
        <button
          type="button"
          className="flex w-full items-center justify-between gap-3 text-left"
          aria-expanded={mobileSettingsOpen}
          aria-controls="mobile-settings-navigation"
          onClick={() => setMobileSettingsOpen((current) => !current)}
        >
          <span className="min-w-0 flex-1">
            <span className="block text-xs font-semibold uppercase text-slate dark:text-slate-400">Settings</span>
            <span className="mt-0.5 block truncate font-semibold text-ink dark:text-slate-100">{activeSettingsLabel}</span>
          </span>
          <span className="shrink-0 text-sm font-semibold text-cyan dark:text-cyan-200">
            {mobileSettingsOpen ? 'Close' : 'Change'}
          </span>
        </button>

        {mobileSettingsOpen && (
          <div id="mobile-settings-navigation" className="mt-3 border-t border-slate/20 pt-2 dark:border-white/10">
            <nav className="divide-y divide-slate/15 dark:divide-white/10" aria-label="Settings sections">
              {navItems.map((item) => {
                const active = isSettingsLinkActive(location.pathname, item.to)
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    aria-current={active ? 'page' : undefined}
                    className={`block border-l-2 px-3 py-3 text-sm transition ${
                      active
                        ? 'border-l-cyan bg-cyan/10 font-semibold text-cyan dark:bg-cyan/10 dark:text-cyan-100'
                        : 'border-l-transparent text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-white/[0.06]'
                    }`}
                  >
                    {item.label}
                  </Link>
                )
              })}
              <div>
                <button
                  type="button"
                  aria-expanded={showIntegrationsChildren}
                  className={`block w-full border-l-2 px-3 py-3 text-left text-sm transition ${
                    integrationsActive
                      ? 'border-l-cyan bg-cyan/10 font-semibold text-cyan dark:bg-cyan/10 dark:text-cyan-100'
                      : 'border-l-transparent text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-white/[0.06]'
                  }`}
                  onClick={() => setIntegrationsExpanded((current) => !current)}
                >
                  Integrations
                </button>
                {showIntegrationsChildren && (
                  <div className="border-t border-slate/10 pl-4 dark:border-white/10">
                    <Link
                      to="/settings/integrations/webhooks"
                      aria-current={webhooksActive ? 'page' : undefined}
                      className={`block border-l-2 px-3 py-3 text-sm transition ${
                        webhooksActive
                          ? 'border-l-cyan bg-cyan/10 font-semibold text-cyan dark:bg-cyan/10 dark:text-cyan-100'
                          : 'border-l-transparent text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-white/[0.06]'
                      }`}
                    >
                      Webhooks
                    </Link>
                    {role === 'admin' && (
                      <Link
                        to="/settings/integrations/smtp"
                        aria-current={smtpActive ? 'page' : undefined}
                        className={`block border-l-2 px-3 py-3 text-sm transition ${
                          smtpActive
                            ? 'border-l-cyan bg-cyan/10 font-semibold text-cyan dark:bg-cyan/10 dark:text-cyan-100'
                            : 'border-l-transparent text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-white/[0.06]'
                        }`}
                      >
                        SMTP
                      </Link>
                    )}
                  </div>
                )}
              </div>
            </nav>
            <p className="mt-3 px-1 text-xs text-slate dark:text-slate-400">
              Current role: <span className="font-semibold text-ink dark:text-slate-200">{role || 'loading...'}</span>
            </p>
          </div>
        )}
      </aside>

      <aside className="tl-surface hidden min-w-0 rounded-xl p-4 lg:block">
        <h2 className="font-display text-xl">Settings</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/70">Manage access, automation, and administration tools from one place.</p>

        <nav className="mt-4">
          <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-1">
            {navItems.map((item) => {
              const active = isSettingsLinkActive(location.pathname, item.to)
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  aria-current={active ? 'page' : undefined}
                  className={`block rounded-lg border px-3 py-2 text-center text-sm transition lg:text-left ${
                    active
                      ? 'tl-nav-link-active font-semibold'
                      : 'border-transparent text-slate hover:border-slate/20 hover:bg-slate/10 dark:text-slate-200 dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06]'
                  }`}
                >
                  {item.label}
                </Link>
              )
            })}
            <div className="min-w-0">
              <button
                type="button"
                aria-expanded={showIntegrationsChildren}
                className={`block w-full rounded-lg border px-3 py-2 text-center text-sm transition lg:text-left ${
                  integrationsActive
                    ? 'tl-nav-link-active font-semibold'
                    : 'border-transparent text-slate hover:border-slate/20 hover:bg-slate/10 dark:text-slate-200 dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06]'
                }`}
                onClick={() => setIntegrationsExpanded((current) => !current)}
              >
                Integrations
              </button>
              {showIntegrationsChildren && (
                <div className="mt-1 grid gap-1 pl-2">
                  <Link
                    to="/settings/integrations/webhooks"
                    aria-current={webhooksActive ? 'page' : undefined}
                    className={`block rounded-lg border px-3 py-1.5 text-center text-sm transition lg:text-left ${
                      webhooksActive
                        ? 'tl-nav-link-active font-semibold'
                        : 'border-transparent text-slate hover:border-slate/20 hover:bg-slate/10 dark:text-slate-200 dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06]'
                    }`}
                  >
                    Webhooks
                  </Link>
                  {role === 'admin' && (
                    <Link
                      to="/settings/integrations/smtp"
                      aria-current={smtpActive ? 'page' : undefined}
                      className={`block rounded-lg border px-3 py-1.5 text-center text-sm transition lg:text-left ${
                        smtpActive
                          ? 'tl-nav-link-active font-semibold'
                          : 'border-transparent text-slate hover:border-slate/20 hover:bg-slate/10 dark:text-slate-200 dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06]'
                      }`}
                    >
                      SMTP
                    </Link>
                  )}
                </div>
              )}
            </div>
          </div>
        </nav>

        <div className="tl-surface-muted mt-5 rounded p-3 text-xs">
          <p className="font-semibold">Current role</p>
          <p className="mt-1 text-cyan-800 dark:text-cyan-200">{role || 'loading...'}</p>
        </div>
      </aside>

      <section className="min-w-0">
        <Outlet />
      </section>
    </div>
  )
}

function isSettingsLinkActive(pathname: string, targetPath: string) {
  return pathname === targetPath || pathname.startsWith(`${targetPath}/`)
}
