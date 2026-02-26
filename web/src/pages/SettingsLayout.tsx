import { Link, Outlet, useLocation } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'

export function SettingsLayout() {
  const meQuery = useCurrentUser()
  const location = useLocation()
  const role = meQuery.data?.role

  const navItems = [
    { to: '/settings', label: 'Overview' },
    { to: '/settings/account', label: 'Account' },
    { to: '/settings/tokens', label: 'API Tokens' },
    ...(role === 'admin' ? [{ to: '/settings/users', label: 'Users' }] : []),
    ...(role === 'admin' ? [{ to: '/settings/audit-logs', label: 'Audit Logs' }] : []),
  ]

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
      <aside className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
        <h2 className="font-display text-xl">Settings</h2>
        <nav className="mt-3 space-y-1">
          {navItems.map((item) => {
            const active = location.pathname === item.to
            return (
              <Link
                key={item.to}
                to={item.to}
                className={`block rounded px-3 py-2 text-sm ${
                  active
                    ? 'bg-cyan/15 text-cyan dark:bg-cyan-900/35 dark:text-cyan-300'
                    : 'text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-cyan-950/40'
                }`}
              >
                {item.label}
              </Link>
            )
          })}
        </nav>

        <div className="mt-5 rounded border border-cyan/20 bg-cyan/10 p-3 text-xs dark:border-cyan-800/40 dark:bg-cyan-950/40">
          <p className="font-semibold">Current role</p>
          <p className="mt-1 text-cyan-800 dark:text-cyan-200">{role || 'loading...'}</p>
        </div>
      </aside>

      <section>
        <Outlet />
      </section>
    </div>
  )
}
