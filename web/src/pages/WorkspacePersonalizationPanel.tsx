import { ArrowDown, ArrowUp, RefreshCw, RotateCcw, Save } from 'lucide-react'

import {
  TRUSTED_DASHBOARD_PANELS,
  TRUSTED_WORKSPACE_MODULE_BY_ID,
  isTrustedWorkspaceModuleId,
} from '../workspace/moduleRegistry'
import { isDashboardPanelAvailable } from '../workspace/workspaceModel'
import type { WorkspaceSettingsController } from './useWorkspaceSettingsController'
import {
  movePersonalModule,
  personalLandingOptions,
  toggleStringValue,
  updatePersonalModule,
} from './workspaceSettingsModel'

export function WorkspacePersonalizationPanel({ controller }: { controller: WorkspaceSettingsController }) {
  const { workspace, personalDraft } = controller
  const effective = workspace.effective
  const preferences = workspace.preferences

  return (
    <section className="tl-surface overflow-hidden rounded-xl" aria-labelledby="personal-workspace-heading">
      <header className="border-b border-slate/20 px-4 py-4 dark:border-white/10 sm:px-5">
        <h2 id="personal-workspace-heading" className="font-display text-lg">My workspace</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          Personal choices can hide or reorder optional modules, but cannot grant access or override organization policy.
        </p>
      </header>

      {!personalDraft || !effective || !preferences ? (
        <div className="px-4 py-6 text-sm text-slate dark:text-slate-300">
          {workspace.error ? 'Personal workspace settings are unavailable until the server can be reached.' : 'Loading personal workspace settings...'}
        </div>
      ) : (
        <div className="space-y-5 px-4 py-4 sm:px-5">
          <PersonalModuleList controller={controller} />
          <PersonalLandingControl controller={controller} />
          <PersonalDashboardControls controller={controller} />

          {controller.personalError && (
            <div role="alert" className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
              <p>{controller.personalError}</p>
              {controller.personalRevisionConflict && (
                <button
                  type="button"
                  className="mt-2 inline-flex min-h-11 items-center justify-center gap-2 rounded border border-red-400/50 px-3 py-2 font-semibold disabled:opacity-60 sm:min-h-0"
                  disabled={controller.personalMutationPending}
                  onClick={() => void controller.discardAndReloadPersonal()}
                >
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  Discard personal changes and reload
                </button>
              )}
            </div>
          )}
          {controller.personalFeedback && (
            <p role="status" className="rounded border border-emerald-300/60 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100">
              {controller.personalFeedback}
            </p>
          )}

          <div className="flex flex-col-reverse gap-2 border-t border-slate/15 pt-4 sm:flex-row sm:justify-end dark:border-white/10">
            <button
              type="button"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0 dark:border-cyan-900/40"
              disabled={
                controller.personalMutationPending ||
                preferences.revision === 0
              }
              onClick={() => controller.setResetPersonalRequested(true)}
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              Reset defaults
            </button>
            <button
              type="button"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
              disabled={
                !controller.personalDirty ||
                controller.personalMutationPending
              }
              onClick={() => void controller.savePersonal()}
            >
              <Save className="h-4 w-4" aria-hidden="true" />
              {workspace.isSavingPreferences ? 'Saving...' : 'Save personal workspace'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function PersonalModuleList({ controller }: { controller: WorkspaceSettingsController }) {
  const draft = controller.personalDraft!
  const effectiveById = new Map(controller.workspace.effective!.modules.map((module) => [module.id, module]))
  const ordered = [...draft.modules.entries()].sort(([leftId, left], [rightId, right]) => {
    const leftSection = TRUSTED_WORKSPACE_MODULE_BY_ID.get(leftId)?.section ?? 'settings'
    const rightSection = TRUSTED_WORKSPACE_MODULE_BY_ID.get(rightId)?.section ?? 'settings'
    return leftSection.localeCompare(rightSection) || left.order - right.order || leftId.localeCompare(rightId)
  })

  return (
    <fieldset disabled={controller.personalMutationPending}>
      <legend className="text-sm font-semibold">Optional modules</legend>
      <p className="mt-1 text-xs text-slate dark:text-slate-400">
        Hidden or unavailable modules never become links. Desktop ordering applies among modules with the same parent.
      </p>
      <div className="mt-3 divide-y divide-slate/15 rounded border border-slate/20 dark:divide-white/10 dark:border-white/10">
        {ordered.map(([moduleId, preference]) => {
          const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)
          const effective = effectiveById.get(moduleId)
          if (!definition || !effective) return null
          const available = effective.policy_visible && effective.permission_allowed && effective.feature_available
          const Icon = definition.icon
          const siblingItems = ordered.filter(
            ([id]) => TRUSTED_WORKSPACE_MODULE_BY_ID.get(id)?.parentId === definition.parentId,
          )
          const siblingIndex = siblingItems.findIndex(([id]) => id === moduleId)
          return (
            <div key={moduleId} className="flex flex-wrap items-center gap-3 px-3 py-3">
              <Icon className="h-4 w-4 shrink-0 text-cyan" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-ink dark:text-slate-100">{definition.label}</p>
                <p className="break-all text-xs text-slate dark:text-slate-400">{moduleId}</p>
                {!available && <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">Unavailable under current policy, permissions, or feature settings.</p>}
              </div>
              <label className="inline-flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
                <input
                  type="checkbox"
                  checked={preference.visible}
                  disabled={!available || controller.personalMutationPending}
                  onChange={(event) => {
                    const visible = event.target.checked
                    controller.setPersonalDraft((current) => {
                      if (!current) return current
                      const updated = updatePersonalModule(current, moduleId, { visible })
                      const landingStillAvailable = personalLandingOptions(
                        controller.workspace.effective!,
                        updated,
                      ).some((module) => module.id === updated.landingModuleId)
                      return updated.landingModuleId &&
                        isTrustedWorkspaceModuleId(updated.landingModuleId) &&
                        !landingStillAvailable
                        ? { ...updated, landingModuleId: null }
                        : updated
                    })
                  }}
                />
                Show
              </label>
              <div className="flex shrink-0 gap-1">
                <OrderButton
                  label={`Move ${definition.label} earlier`}
                  disabled={siblingIndex === 0 || controller.personalMutationPending}
                  onClick={() => controller.setPersonalDraft((current) => current ? movePersonalModule(current, moduleId, -1) : current)}
                >
                  <ArrowUp className="h-4 w-4" aria-hidden="true" />
                </OrderButton>
                <OrderButton
                  label={`Move ${definition.label} later`}
                  disabled={siblingIndex === siblingItems.length - 1 || controller.personalMutationPending}
                  onClick={() => controller.setPersonalDraft((current) => current ? movePersonalModule(current, moduleId, 1) : current)}
                >
                  <ArrowDown className="h-4 w-4" aria-hidden="true" />
                </OrderButton>
              </div>
            </div>
          )
        })}
      </div>
    </fieldset>
  )
}

function OrderButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      className="inline-flex h-11 w-11 items-center justify-center rounded border border-slate/25 text-slate disabled:opacity-35 sm:h-10 sm:w-10 dark:border-white/10 dark:text-slate-200"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

function PersonalLandingControl({ controller }: { controller: WorkspaceSettingsController }) {
  const draft = controller.personalDraft!
  const options = personalLandingOptions(controller.workspace.effective!, draft)
  const trustedCurrent = draft.landingModuleId === null || options.some((module) => module.id === draft.landingModuleId)

  return (
    <label className="block max-w-xl text-sm font-semibold">
      Landing module
      <select
        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
        value={draft.landingModuleId ?? ''}
        disabled={controller.personalMutationPending}
        onChange={(event) => controller.setPersonalDraft((current) => current ? {
          ...current,
          landingModuleId: event.target.value || null,
        } : current)}
      >
        <option value="">Use organization default</option>
        {!trustedCurrent && draft.landingModuleId && (
          <option value={draft.landingModuleId}>Unavailable in this frontend (preserved)</option>
        )}
        {options.map((module) => <option key={module.id} value={module.id}>{module.label}</option>)}
      </select>
      <span className="mt-1 block text-xs font-normal text-slate dark:text-slate-400">
        Used after local sign-in and whenever ThreatLens opens the workspace start route.
      </span>
    </label>
  )
}

function PersonalDashboardControls({ controller }: { controller: WorkspaceSettingsController }) {
  const draft = controller.personalDraft!
  return (
    <fieldset disabled={controller.personalMutationPending}>
      <legend className="text-sm font-semibold">First-use dashboard defaults</legend>
      <p className="mt-1 text-xs text-slate dark:text-slate-400">
        These panels seed a new or reset local dashboard. Existing saved layouts are not replaced.
      </p>
      <label className="mt-2 inline-flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
        <input
          type="checkbox"
          checked={draft.inheritDashboardPanels}
          disabled={controller.personalMutationPending}
          onChange={(event) => controller.setPersonalDraft((current) => current ? {
            ...current,
            inheritDashboardPanels: event.target.checked,
          } : current)}
        />
        Use organization defaults
      </label>
      {!draft.inheritDashboardPanels && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {TRUSTED_DASHBOARD_PANELS.map((panel) => {
            const available = isDashboardPanelAvailable(panel, controller.workspace.userContext)
            const Icon = panel.icon
            return (
              <label key={panel.id} className="flex min-h-11 items-center gap-2 rounded border border-slate/20 px-3 py-2 text-sm dark:border-white/10">
                <input
                  type="checkbox"
                  checked={draft.dashboardPanelIds.includes(panel.id)}
                  disabled={
                    !available ||
                    controller.personalMutationPending ||
                    (draft.dashboardPanelIds.length === 1 && draft.dashboardPanelIds.includes(panel.id))
                  }
                  onChange={(event) => controller.setPersonalDraft((current) => current ? {
                    ...current,
                    dashboardPanelIds: toggleStringValue(current.dashboardPanelIds, panel.id, event.target.checked),
                  } : current)}
                />
                <Icon className="h-4 w-4 text-cyan" aria-hidden="true" />
                <span>{panel.label}</span>
                {!available && <span className="ml-auto text-xs text-slate dark:text-slate-400">Unavailable</span>}
              </label>
            )
          })}
        </div>
      )}
    </fieldset>
  )
}
