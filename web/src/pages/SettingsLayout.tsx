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
        ...(role === 'admin' && aiEnabled ? [{ to: '/settings/ai', label: 'AI' }] : []),
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
      <aside className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-white/10 dark:bg-slate-950/65">
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
                  const active = isSettingsLinkActive(location.pathname, item.to)
                  return (
                    <Link
                      key={item.to}
                      to={item.to}
                      aria-current={active ? 'page' : undefined}
                      className={`block rounded-lg border px-3 py-2 text-center text-sm transition lg:text-left ${
                        active
                          ? 'border-cyan/40 bg-cyan/10 font-semibold text-cyan shadow-[inset_0_0_0_1px_rgba(8,145,178,0.12)] ring-1 ring-cyan/10 dark:border-cyan-500/40 dark:bg-cyan-500/15 dark:text-cyan-100'
                          : 'border-transparent text-slate hover:border-slate/20 hover:bg-slate/10 dark:text-slate-200 dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06]'
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

        <div className="mt-5 rounded border border-cyan/20 bg-cyan/10 p-3 text-xs dark:border-cyan-500/30 dark:bg-cyan-500/12">
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

function isSettingsLinkActive(pathname: string, targetPath: string) {
  if (targetPath === '/settings') {
    return pathname === '/settings'
  }
  return pathname === targetPath || pathname.startsWith(`${targetPath}/`)
}
