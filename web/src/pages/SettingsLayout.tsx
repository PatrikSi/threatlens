import { Link, Outlet, useLocation } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'

export function SettingsLayout() {
  const meQuery = useCurrentUser()
  const location = useLocation()
  const role = meQuery.data?.role
  const aiEnabled = meQuery.data?.features.ai_enabled ?? false

  const navSections = [
    {
      label: 'Overview',
      description: 'Start here',
      items: [{ to: '/settings', label: 'Overview' }],
    },
    {
      label: 'Personal',
      description: 'Your access',
      items: [
        { to: '/settings/account', label: 'Account' },
        { to: '/settings/tokens', label: 'API Tokens' },
      ],
    },
    {
      label: 'Automation',
      description: 'Signals and automation',
      items: [
        { to: '/settings/notifications', label: 'Notifications' },
        ...(role === 'admin' && aiEnabled ? [{ to: '/ai', label: 'AI & Briefing' }] : []),
        ...(role === 'admin' ? [{ to: '/settings/tagging', label: 'Tagging' }] : []),
      ],
    },
    {
      label: 'Administration',
      description: 'Admin only',
      items: [
        ...(role === 'admin' ? [{ to: '/settings/users', label: 'Users' }] : []),
        ...(role === 'admin' ? [{ to: '/settings/audit-logs', label: 'Audit Logs' }] : []),
      ],
    },
  ].filter((section) => section.items.length > 0)

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
      <aside className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Settings</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/70">Manage personal access, automation, and administration tools from one place.</p>

        <nav className="mt-4 space-y-4">
          {navSections.map((section) => (
            <div key={section.label}>
              <div className="mb-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate dark:text-white/55">{section.label}</p>
                <p className="text-xs text-slate dark:text-white/50">{section.description}</p>
              </div>
              <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-1">
                {section.items.map((item) => {
                  const active = location.pathname === item.to
                  return (
                    <Link
                      key={item.to}
                      to={item.to}
                      className={`block rounded px-3 py-2 text-center text-sm lg:text-left ${
                        active
                          ? 'bg-cyan/15 text-cyan dark:bg-cyan-900/35 dark:text-cyan-300'
                          : 'text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-cyan-950/40'
                      }`}
                    >
                      {item.label}
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}
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
