import { Link, Outlet, useLocation } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'

export function SettingsLayout() {
  const meQuery = useCurrentUser()
  const location = useLocation()
  const role = meQuery.data?.role
  const aiEnabled = meQuery.data?.features.ai_enabled ?? false

  const navItems = [
    { to: '/settings/account', label: 'Account' },
    { to: '/settings/tokens', label: 'API Tokens' },
    { to: '/settings/notifications', label: 'Notifications' },
    ...(role === 'admin' && aiEnabled ? [{ to: '/settings/ai', label: 'AI' }] : []),
    ...(role === 'admin' ? [{ to: '/settings/tagging', label: 'Tagging' }] : []),
    ...(role === 'admin' ? [{ to: '/settings/users', label: 'Users' }] : []),
    ...(role === 'admin' ? [{ to: '/settings/audit-logs', label: 'Audit Logs' }] : []),
  ]

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="tl-surface min-w-0 rounded-xl p-4">
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
