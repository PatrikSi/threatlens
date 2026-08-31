import type { ReactNode } from 'react'

export function SettingsPageHeader({
  actions,
  badges,
  children,
  description,
  scope,
  title,
}: {
  actions?: ReactNode
  badges?: ReactNode
  children?: ReactNode
  description: string
  scope: string
  title: string
}) {
  return (
    <header className="tl-surface overflow-hidden rounded-xl">
      <div className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-start sm:justify-between sm:px-5 sm:py-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-slate/20 bg-slate/5 px-2 py-0.5 text-xs font-semibold text-slate dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-300">
              {scope}
            </span>
            {badges}
          </div>
          <h1 className="mt-1.5 font-display text-xl text-ink dark:text-white sm:text-2xl">
            {title}
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate dark:text-slate-300">
            {description}
          </p>
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </div>
      {children && (
        <div className="border-t border-slate/15 px-4 dark:border-white/10 sm:px-5">
          {children}
        </div>
      )}
    </header>
  )
}

export function SettingsReadOnlyNotice({ permission }: { permission: string }) {
  return (
    <div
      role="status"
      className="rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
    >
      <p className="font-semibold">Read-only access</p>
      <p className="mt-0.5">You can review this configuration, but changes require {permission}.</p>
    </div>
  )
}
