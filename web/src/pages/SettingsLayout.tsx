import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'
import { useWorkspace } from '../workspace/useWorkspace'
import type { ResolvedWorkspaceModule } from '../workspace/workspaceModel'

type SettingsNavigationVariant = 'desktop' | 'mobile'

export function SettingsLayout() {
  const meQuery = useCurrentUser()
  const workspace = useWorkspace()
  const location = useLocation()
  const [integrationsExpanded, setIntegrationsExpanded] = useState(false)
  const [mobileSettingsOpen, setMobileSettingsOpen] = useState(false)
  const roleLabel = meQuery.isError ? 'unavailable' : meQuery.data?.role ?? 'loading...'
  const integrationsActive = isSettingsLinkActive(location.pathname, '/settings/integrations')
  const showIntegrationsChildren = integrationsActive || integrationsExpanded
  const activeModule = workspace.model.settingsNavigation
    .filter((module) => isSettingsLinkActive(location.pathname, module.route))
    .sort((left, right) => right.route.length - left.route.length)[0]
  const activeSettingsLabel = activeModule?.parentId === 'settings.integrations'
    ? `Integrations / ${activeModule.label}`
    : activeModule?.label ?? 'Settings'

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
            <span className="mt-0.5 block truncate font-semibold text-ink dark:text-slate-100">
              {activeSettingsLabel}
            </span>
          </span>
          <span className="shrink-0 text-sm font-semibold text-cyan dark:text-cyan-200">
            {mobileSettingsOpen ? 'Close' : 'Change'}
          </span>
        </button>

        {mobileSettingsOpen && (
          <div id="mobile-settings-navigation" className="mt-3 border-t border-slate/20 pt-2 dark:border-white/10">
            <nav className="divide-y divide-slate/15 dark:divide-white/10" aria-label="Settings sections">
              <SettingsNavigationItems
                modules={workspace.model.mobileSettingsNavigation}
                pathname={location.pathname}
                integrationsExpanded={showIntegrationsChildren}
                onToggleIntegrations={() => setIntegrationsExpanded((current) => !current)}
                variant="mobile"
              />
            </nav>
            <p className="mt-3 px-1 text-xs text-slate dark:text-slate-400">
              Current role: <span className="font-semibold text-ink dark:text-slate-200">{roleLabel}</span>
            </p>
          </div>
        )}
      </aside>

      <aside className="tl-surface hidden min-w-0 rounded-xl p-4 lg:block">
        <h2 className="font-display text-xl">Settings</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/70">
          Manage access, automation, and administration tools from one place.
        </p>

        <nav id="desktop-settings-navigation" className="mt-4" aria-label="Settings sections">
          <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-1">
            <SettingsNavigationItems
              modules={workspace.model.settingsNavigation}
              pathname={location.pathname}
              integrationsExpanded={showIntegrationsChildren}
              onToggleIntegrations={() => setIntegrationsExpanded((current) => !current)}
              variant="desktop"
            />
          </div>
        </nav>

        <div className="tl-surface-muted mt-5 rounded p-3 text-xs">
          <p className="font-semibold">Current role</p>
          <p className="mt-1 text-cyan-800 dark:text-cyan-200">{roleLabel}</p>
        </div>
      </aside>

      <section className="min-w-0">
        <Outlet />
      </section>
    </div>
  )
}

function SettingsNavigationItems({
  modules,
  pathname,
  integrationsExpanded,
  onToggleIntegrations,
  variant,
}: {
  modules: readonly ResolvedWorkspaceModule[]
  pathname: string
  integrationsExpanded: boolean
  onToggleIntegrations: () => void
  variant: SettingsNavigationVariant
}) {
  const integrationChildren = modules.filter((module) => module.parentId === 'settings.integrations')
  const topLevelModules = modules.filter(
    (module) =>
      module.parentId === 'primary.settings' &&
      (module.id !== 'settings.integrations' || integrationChildren.length > 0),
  )

  return topLevelModules.map((module) => {
    if (module.id === 'settings.integrations') {
      return (
        <IntegrationNavigationGroup
          key={module.id}
          module={module}
          childrenModules={integrationChildren}
          pathname={pathname}
          expanded={integrationsExpanded}
          onToggle={onToggleIntegrations}
          variant={variant}
        />
      )
    }
    return <SettingsNavigationLink key={module.id} module={module} pathname={pathname} variant={variant} />
  })
}

function IntegrationNavigationGroup({
  module,
  childrenModules,
  pathname,
  expanded,
  onToggle,
  variant,
}: {
  module: ResolvedWorkspaceModule
  childrenModules: readonly ResolvedWorkspaceModule[]
  pathname: string
  expanded: boolean
  onToggle: () => void
  variant: SettingsNavigationVariant
}) {
  const active = isSettingsLinkActive(pathname, module.route)
  const mobile = variant === 'mobile'
  const childRegionId = `${variant}-settings-integrations-children`

  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={childRegionId}
        className={mobile ? mobileNavigationClass(active) : desktopNavigationClass(active)}
        onClick={onToggle}
      >
        {module.label}
      </button>
      {expanded && (
        <div
          id={childRegionId}
          className={mobile ? 'border-t border-slate/10 pl-4 dark:border-white/10' : 'mt-1 grid gap-1 pl-2'}
        >
          {childrenModules.map((child) => (
            <SettingsNavigationLink
              key={child.id}
              module={child}
              pathname={pathname}
              variant={variant}
              nested
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SettingsNavigationLink({
  module,
  pathname,
  variant,
  nested = false,
}: {
  module: ResolvedWorkspaceModule
  pathname: string
  variant: SettingsNavigationVariant
  nested?: boolean
}) {
  const active = isSettingsLinkActive(pathname, module.route)
  const className = variant === 'mobile'
    ? mobileNavigationClass(active)
    : desktopNavigationClass(active, nested)

  return (
    <Link to={module.route} aria-current={active ? 'page' : undefined} className={className}>
      {module.label}
    </Link>
  )
}

function mobileNavigationClass(active: boolean) {
  return `block w-full border-l-2 px-3 py-3 text-left text-sm transition ${
    active
      ? 'border-l-cyan bg-cyan/10 font-semibold text-cyan dark:bg-cyan/10 dark:text-cyan-100'
      : 'border-l-transparent text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-white/[0.06]'
  }`
}

function desktopNavigationClass(active: boolean, nested = false) {
  return `block w-full rounded-lg border px-3 ${nested ? 'py-1.5' : 'py-2'} text-center text-sm transition lg:text-left ${
    active
      ? 'tl-nav-link-active font-semibold'
      : 'border-transparent text-slate hover:border-slate/20 hover:bg-slate/10 dark:text-slate-200 dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06]'
  }`
}

function isSettingsLinkActive(pathname: string, targetPath: string) {
  return pathname === targetPath || pathname.startsWith(`${targetPath}/`)
}
