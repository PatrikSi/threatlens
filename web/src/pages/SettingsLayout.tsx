import { ChevronDown } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'

import { useCurrentUser } from '../hooks/useCurrentUser'
import {
  SETTINGS_PRESENTATION_GROUPS,
  formatSettingsRoleLabel,
  settingsModulePresentation,
} from '../workspace/modulePresentation'
import { useWorkspace } from '../workspace/useWorkspace'
import type { ResolvedWorkspaceModule } from '../workspace/workspaceModel'

type SettingsNavigationVariant = 'desktop' | 'mobile'

export function SettingsLayout() {
  const meQuery = useCurrentUser()
  const workspace = useWorkspace()
  const location = useLocation()
  const [integrationsExpanded, setIntegrationsExpanded] = useState(false)
  const [mobileSettingsOpen, setMobileSettingsOpen] = useState(false)
  const roleLabel = meQuery.isError
    ? 'Unavailable'
    : meQuery.data?.role
      ? formatSettingsRoleLabel(meQuery.data.role)
      : 'Loading…'
  const navigationModules = workspace.model.settingsNavigation
  const integrationsActive = isSettingsLinkActive(
    location.pathname,
    '/settings/integrations',
  )
  const showIntegrationsChildren = integrationsActive || integrationsExpanded
  const activeModule = navigationModules
    .filter((module) => isSettingsLinkActive(location.pathname, module.route))
    .sort((left, right) => right.route.length - left.route.length)[0]
  const activeSettingsLabel = activeModule
    ? settingsModulePresentation(activeModule.id, activeModule.label).label
    : 'Settings'

  useEffect(() => {
    setMobileSettingsOpen(false)
  }, [location.pathname])

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[248px_minmax(0,1fr)]">
      <aside className="tl-surface min-w-0 rounded-lg p-3 lg:hidden">
        <button
          type="button"
          className="flex w-full items-center gap-3 text-left"
          aria-expanded={mobileSettingsOpen}
          aria-controls="mobile-settings-navigation"
          aria-label={`${mobileSettingsOpen ? 'Close' : 'Open'} settings navigation. Current section: ${activeSettingsLabel}`}
          onClick={() => setMobileSettingsOpen((current) => !current)}
        >
          <span className="min-w-0 flex-1">
            <span className="block text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-400">
              Settings
            </span>
            <span className="mt-0.5 block truncate font-semibold text-ink dark:text-slate-100">
              {activeSettingsLabel}
            </span>
          </span>
          <span className="rounded-full border border-slate/20 px-2 py-0.5 text-xs font-semibold text-slate dark:border-white/10 dark:text-slate-300">
            {roleLabel}
          </span>
          <ChevronDown
            className={`h-4 w-4 shrink-0 text-slate transition-transform dark:text-slate-300 ${
              mobileSettingsOpen ? 'rotate-180' : ''
            }`}
            aria-hidden="true"
          />
        </button>

        {mobileSettingsOpen && (
          <div
            id="mobile-settings-navigation"
            className="mt-3 border-t border-slate/20 pt-3 dark:border-white/10"
          >
            <nav aria-label="Settings sections">
              <SettingsNavigationItems
                modules={navigationModules}
                pathname={location.pathname}
                integrationsExpanded={showIntegrationsChildren}
                onToggleIntegrations={() =>
                  setIntegrationsExpanded((current) => !current)
                }
                variant="mobile"
              />
            </nav>
          </div>
        )}
      </aside>

      <aside className="tl-surface sticky top-4 hidden max-h-[calc(100vh-2rem)] min-w-0 self-start overflow-y-auto rounded-xl p-3 lg:block">
        <div className="flex items-center justify-between gap-2 px-1">
          <h2 className="font-display text-lg">Settings</h2>
          <span className="rounded-full border border-slate/20 px-2 py-0.5 text-xs font-semibold text-slate dark:border-white/10 dark:text-slate-300">
            {roleLabel}
          </span>
        </div>
        <p className="mt-1 px-1 text-xs leading-5 text-slate dark:text-white/65">
          Personal and administrative controls.
        </p>

        <nav
          id="desktop-settings-navigation"
          className="mt-4"
          aria-label="Settings sections"
        >
          <SettingsNavigationItems
            modules={navigationModules}
            pathname={location.pathname}
            integrationsExpanded={showIntegrationsChildren}
            onToggleIntegrations={() =>
              setIntegrationsExpanded((current) => !current)
            }
            variant="desktop"
          />
        </nav>
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
  const integrationChildren = modules.filter(
    (module) => module.parentId === 'settings.integrations',
  )
  const topLevelModules = modules.filter(
    (module) =>
      module.parentId === 'primary.settings' &&
      (module.id !== 'settings.integrations' || integrationChildren.length > 0),
  )
  const groups = SETTINGS_PRESENTATION_GROUPS.map((group) => ({
    ...group,
    modules: topLevelModules.filter(
      (module) =>
        settingsModulePresentation(module.id, module.label).groupId === group.id,
    ),
  })).filter((group) => group.modules.length > 0)

  return (
    <div className="space-y-3">
      {groups.map((group) => {
        const headingId = `${variant}-settings-group-${group.id}`
        return (
          <section key={group.id} aria-labelledby={headingId}>
            <h3
              id={headingId}
              className="px-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate dark:text-slate-400"
            >
              {group.label}
            </h3>
            <div className="mt-1 space-y-0.5">
              {group.modules.map((module) => {
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
                return (
                  <SettingsNavigationLink
                    key={module.id}
                    module={module}
                    pathname={pathname}
                    variant={variant}
                  />
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
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
  const presentation = settingsModulePresentation(module.id, module.label)
  const Icon = module.icon

  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={childRegionId}
        aria-label={`${expanded ? 'Collapse' : 'Expand'} ${presentation.label} settings`}
        className={
          mobile
            ? mobileNavigationClass(active)
            : desktopNavigationClass(active)
        }
        onClick={onToggle}
      >
        {Icon && <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />}
        <span className="min-w-0 flex-1 truncate">{presentation.label}</span>
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>
      {expanded && (
        <div
          id={childRegionId}
          className="mt-0.5 space-y-0.5 border-l border-slate/15 pl-3 dark:border-white/10"
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
  const presentation = settingsModulePresentation(module.id, module.label)
  const Icon = module.icon
  const className =
    variant === 'mobile'
      ? mobileNavigationClass(active, nested)
      : desktopNavigationClass(active, nested)

  return (
    <Link
      to={module.route}
      aria-current={active ? 'page' : undefined}
      className={className}
    >
      {Icon && (
        <Icon
          className={`${nested ? 'h-3.5 w-3.5' : 'h-4 w-4'} shrink-0`}
          aria-hidden="true"
        />
      )}
      <span className="min-w-0 truncate">{presentation.label}</span>
    </Link>
  )
}

function mobileNavigationClass(active: boolean, nested = false) {
  return `flex min-h-11 w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left text-sm transition ${
    nested ? 'text-[13px]' : ''
  } ${
    active
      ? 'tl-nav-link-active font-semibold'
      : 'border-transparent text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-white/[0.06]'
  }`
}

function desktopNavigationClass(active: boolean, nested = false) {
  return `flex min-h-9 w-full items-center gap-2 rounded-md border px-2.5 ${
    nested ? 'py-1 text-[13px]' : 'py-1.5 text-sm'
  } text-left transition ${
    active
      ? 'tl-nav-link-active font-semibold'
      : 'border-transparent text-slate hover:border-slate/20 hover:bg-slate/10 dark:text-slate-200 dark:hover:border-cyan-500/35 dark:hover:bg-white/[0.06]'
  }`
}

function isSettingsLinkActive(pathname: string, targetPath: string) {
  return pathname === targetPath || pathname.startsWith(`${targetPath}/`)
}
