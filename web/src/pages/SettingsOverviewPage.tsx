export function SettingsOverviewPage() {
  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Role Capabilities</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Use this reference to understand who can operate which controls.
        </p>
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <RoleCard
          title="Admin"
          color="cyan"
          items={[
            'Manage users and role assignments',
            'Access and query audit logs',
            'Create/revoke own and other users\' API tokens',
            'Full feed and triage management',
          ]}
        />
        <RoleCard
          title="Analyst"
          color="emerald"
          items={[
            'Manage feeds and triage actions',
            'Create/revoke personal API tokens',
            'No access to global user admin',
            'No access to global audit logs',
          ]}
        />
        <RoleCard
          title="Viewer"
          color="amber"
          items={[
            'Read-only dashboard and feeds',
            'Can access personal account settings',
            'Can create/revoke personal API tokens',
            'Cannot mutate feeds, tags, or triage state',
          ]}
        />
      </div>
    </div>
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
