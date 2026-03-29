import { Link } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'

export function SettingsOverviewPage() {
  const meQuery = useCurrentUser()
  const role = meQuery.data?.role
  const aiEnabled = meQuery.data?.features.ai_enabled ?? false

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-white/55">Settings Map</p>
        <h2 className="mt-1 font-display text-xl">Organize access, automation, and administration</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Use this page to orient yourself before diving into a specific settings area. Personal access, automation, and admin
          controls are grouped separately so it is easier to find the right surface quickly.
        </p>
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <SettingsAreaCard
          title="Personal Access"
          subtitle="Account and personal credentials"
          accent="cyan"
          availability="Available to every signed-in user"
          links={[
            { to: '/settings/account', label: 'Open Account' },
            { to: '/settings/tokens', label: 'Manage API Tokens' },
          ]}
          items={[
            'Review your profile and password settings',
            'Create and revoke your own API tokens',
            'Keep your personal access separate from admin automation controls',
          ]}
        />
        <SettingsAreaCard
          title="Automation"
          subtitle="Delivery, AI, and content automation"
          accent="emerald"
          availability={role === 'admin' ? 'Notifications for everyone, AI and tagging for admins' : 'Notifications available to you'}
          links={[
            { to: '/settings/notifications', label: 'Open Notifications' },
            ...(role === 'admin' && aiEnabled ? [{ to: '/ai', label: 'Open AI & Briefing' }] : []),
            ...(role === 'admin' ? [{ to: '/settings/tagging', label: 'Open Tagging' }] : []),
          ]}
          items={[
            'Configure outgoing webhook notifications',
            ...(role === 'admin' && aiEnabled ? ['Tune AI summaries, relevance scoring, and daily briefing behavior'] : []),
            ...(role === 'admin' ? ['Control auto-tagging defaults and custom tagging rules'] : []),
          ]}
        />
        <SettingsAreaCard
          title="Administration"
          subtitle="User management and auditability"
          accent="amber"
          availability={role === 'admin' ? 'Admin only' : 'Visible to admins only'}
          links={
            role === 'admin'
              ? [
                  { to: '/settings/users', label: 'Manage Users' },
                  { to: '/settings/audit-logs', label: 'Review Audit Logs' },
                ]
              : []
          }
          items={[
            'Manage role assignments and user approval workflows',
            'Inspect system audit trails and administrative activity',
            'Keep operational oversight separate from daily analyst workflows',
          ]}
        />
      </div>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Role Capabilities</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Use this reference to understand who can operate which controls across the settings areas above.
        </p>
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <RoleCard
          title="Admin"
          color="cyan"
          items={[
            'Manage users and role assignments',
            'Access and query audit logs',
            'Create and revoke own and other users\' API tokens',
            'Configure personal webhook notifications',
            'Tune global auto-tagging and manage custom tag rules',
            'Access AI automation and briefing settings when AI is enabled',
            'Full feed and triage management',
          ]}
        />
        <RoleCard
          title="Analyst"
          color="emerald"
          items={[
            'Manage feeds and triage actions',
            'Create and revoke personal API tokens',
            'Configure personal webhook notifications',
            'No access to global user admin',
            'No access to global audit logs',
            'No access to global tagging or AI automation controls',
          ]}
        />
        <RoleCard
          title="Viewer"
          color="amber"
          items={[
            'Read-only dashboard and feeds',
            'Can access personal account settings',
            'Can create and revoke personal API tokens',
            'Can configure personal webhook notifications',
            'Cannot mutate feeds, tags, or triage state',
          ]}
        />
      </div>
    </div>
  )
}

function SettingsAreaCard({
  title,
  subtitle,
  accent,
  availability,
  links,
  items,
}: {
  title: string
  subtitle: string
  accent: 'cyan' | 'emerald' | 'amber'
  availability: string
  links: Array<{ to: string; label: string }>
  items: string[]
}) {
  const accentClassName =
    accent === 'cyan'
      ? 'border-cyan/30 bg-cyan/10 dark:border-cyan-800/40 dark:bg-cyan-950/30'
      : accent === 'emerald'
        ? 'border-emerald-300/40 bg-emerald-100/40 dark:border-emerald-900/40 dark:bg-emerald-950/25'
        : 'border-amber-300/40 bg-amber-100/40 dark:border-amber-900/40 dark:bg-amber-950/25'

  return (
    <section className={`rounded-xl border p-4 text-slate-900 dark:text-white ${accentClassName}`}>
      <p className="text-xs font-semibold uppercase tracking-wide text-slate/80 dark:text-white/55">{subtitle}</p>
      <h3 className="mt-1 font-display text-lg">{title}</h3>
      <p className="mt-1 text-sm text-slate dark:text-white/70">{availability}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {links.map((link) => (
          <Link
            key={link.to}
            to={link.to}
            className="rounded-full border border-current/20 bg-white/70 px-3 py-1 text-xs font-semibold dark:bg-[#041612]/50"
          >
            {link.label}
          </Link>
        ))}
      </div>
      <ul className="mt-4 list-disc space-y-1 pl-4 text-sm">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  )
}

function RoleCard({ title, color, items }: { title: string; color: 'cyan' | 'emerald' | 'amber'; items: string[] }) {
  const accent =
    color === 'cyan'
      ? 'border-cyan/30 bg-cyan/10 dark:border-cyan-800/40 dark:bg-cyan-950/30'
      : color === 'emerald'
        ? 'border-emerald-300/40 bg-emerald-100/40 dark:border-emerald-900/40 dark:bg-emerald-950/25'
        : 'border-amber-300/40 bg-amber-100/40 dark:border-amber-900/40 dark:bg-amber-950/25'

  return (
    <section className={`rounded-xl border p-4 text-slate-900 dark:text-white ${accent}`}>
      <h3 className="font-display text-lg">{title}</h3>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-sm">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  )
}
