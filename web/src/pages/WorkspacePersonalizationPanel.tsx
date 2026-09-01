import { ArrowDown, ArrowUp, RefreshCw, RotateCcw, Save } from 'lucide-react'
import { useRef, useState } from 'react'

import {
  TRUSTED_DASHBOARD_PANELS,
  TRUSTED_WORKSPACE_MODULE_BY_ID,
  isTrustedWorkspaceModuleId,
  type TrustedWorkspaceModule,
} from '../workspace/moduleRegistry'
import {
  WORKSPACE_NAVIGATION_GROUPS,
  formatSettingsRoleLabel,
  workspaceModuleDisplayLabel,
  workspaceNavigationGroupOrder,
  workspaceNavigationGroupPresentation,
} from '../workspace/modulePresentation'
import {
  isDashboardPanelAvailable,
  type ResolvedWorkspaceModule,
} from '../workspace/workspaceModel'
import type { WorkspaceSettingsController } from './useWorkspaceSettingsController'
import {
  personalLandingOptions,
  personalNavigationPreview,
  reorderPersonalModule,
  toggleStringValue,
  updatePersonalModule,
} from './workspaceSettingsModel'
import {
  NavigationDragHandle,
  NavigationOrderButton,
} from './WorkspaceNavigationReorderControls'

export function WorkspacePersonalizationPanel({ controller }: { controller: WorkspaceSettingsController }) {
  const { workspace, personalDraft } = controller
  const effective = workspace.effective
  const preferences = workspace.preferences

  return (
    <section className="tl-surface overflow-hidden rounded-xl" aria-labelledby="personal-workspace-heading">
      <header className="border-b border-slate/20 px-4 py-3.5 dark:border-white/10">
        <h2 id="personal-workspace-heading" className="font-display text-lg">My navigation</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          Choose what appears in your navigation and where ThreatLens opens. These preferences cannot grant access or override organization policy.
        </p>
      </header>

      {!personalDraft || !effective || !preferences ? (
        <div className="px-4 py-4 text-sm text-slate dark:text-slate-300">
          {workspace.error ? 'Personal navigation settings are unavailable until the server can be reached.' : 'Loading personal navigation settings...'}
        </div>
      ) : (
        <div className="space-y-4 px-4 py-3.5">
          <PersonalModuleList controller={controller} />
          <PersonalNavigationStructurePreview controller={controller} />
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

          <div className="flex flex-col-reverse gap-2 border-t border-slate/15 pt-3 sm:flex-row sm:justify-end dark:border-white/10">
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
              Reset to organization defaults
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
              {workspace.isSavingPreferences ? 'Saving...' : 'Save navigation preferences'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function PersonalModuleList({ controller }: { controller: WorkspaceSettingsController }) {
  const instructionsId = 'personal-navigation-reorder-instructions'
  const draft = controller.personalDraft!
  const resolvedById = new Map(
    controller.workspace.model.modules.map((module) => [module.id, module]),
  )
  const [draggedModuleId, setDraggedModuleId] = useState<TrustedWorkspaceModule['id'] | null>(null)
  const [dropTargetModuleId, setDropTargetModuleId] = useState<TrustedWorkspaceModule['id'] | null>(null)
  const [keyboardGrabbedModuleId, setKeyboardGrabbedModuleId] = useState<TrustedWorkspaceModule['id'] | null>(null)
  const [reorderStatus, setReorderStatus] = useState(
    'Order changes remain unsaved until you save navigation preferences.',
  )
  const dropHandled = useRef(false)
  const ordered = [...draft.modules.entries()].sort(([leftId, left], [rightId, right]) => {
    const leftDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(leftId)
    const rightDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(rightId)
    const groupOrder = leftDefinition && rightDefinition
      ? workspaceNavigationGroupOrder(leftDefinition) - workspaceNavigationGroupOrder(rightDefinition)
      : 0
    return groupOrder || left.order - right.order || leftId.localeCompare(rightId)
  })

  function moveModule(moduleId: TrustedWorkspaceModule['id'], direction: -1 | 1) {
    const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)
    if (!definition || !isPersonalModuleAvailable(resolvedById.get(moduleId))) return
    const groupId = workspaceNavigationGroupPresentation(definition).id
    const siblings = ordered.filter(
      ([id]) => {
        const candidate = TRUSTED_WORKSPACE_MODULE_BY_ID.get(id)
        return candidate &&
          isPersonalModuleAvailable(resolvedById.get(id)) &&
          candidate.parentId === definition.parentId &&
          workspaceNavigationGroupPresentation(candidate).id === groupId
      },
    )
    const currentIndex = siblings.findIndex(([id]) => id === moduleId)
    const nextIndex = currentIndex + direction
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= siblings.length) return

    controller.setPersonalDraft((current) =>
      current
        ? reorderPersonalModule(current, moduleId, siblings[nextIndex]![0])
        : current,
    )
    setReorderStatus(
      `Moved ${workspaceModuleDisplayLabel(definition)} to position ${nextIndex + 1} of ${siblings.length} in ${workspaceModuleReorderGroupLabel(definition)}. Save navigation preferences to apply this order.`,
    )
  }

  function dropModule(
    sourceId: TrustedWorkspaceModule['id'],
    targetId: TrustedWorkspaceModule['id'],
  ) {
    const sourceDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(sourceId)
    const targetDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(targetId)
    if (
      !sourceDefinition ||
      !targetDefinition ||
      !isPersonalModuleAvailable(resolvedById.get(sourceId)) ||
      !isPersonalModuleAvailable(resolvedById.get(targetId))
    ) return
    if (
      sourceDefinition.parentId !== targetDefinition.parentId ||
      workspaceNavigationGroupPresentation(sourceDefinition).id !==
      workspaceNavigationGroupPresentation(targetDefinition).id
    ) {
      setReorderStatus(
        `${workspaceModuleDisplayLabel(sourceDefinition)} can only be moved within ${workspaceModuleReorderGroupLabel(sourceDefinition)}.`,
      )
      return
    }

    const groupId = workspaceNavigationGroupPresentation(sourceDefinition).id
    const siblings = ordered.filter(([id]) => {
      const candidate = TRUSTED_WORKSPACE_MODULE_BY_ID.get(id)
      return candidate &&
        isPersonalModuleAvailable(resolvedById.get(id)) &&
        candidate.parentId === sourceDefinition.parentId &&
        workspaceNavigationGroupPresentation(candidate).id === groupId
    })
    const targetIndex = siblings.findIndex(([id]) => id === targetId)
    if (targetIndex < 0 || sourceId === targetId) {
      setReorderStatus(`${workspaceModuleDisplayLabel(sourceDefinition)} stayed in its current position.`)
      return
    }
    controller.setPersonalDraft((current) =>
      current ? reorderPersonalModule(current, sourceId, targetId) : current,
    )
    setReorderStatus(
      `Moved ${workspaceModuleDisplayLabel(sourceDefinition)} to position ${targetIndex + 1} of ${siblings.length} in ${workspaceModuleReorderGroupLabel(sourceDefinition)}. Save navigation preferences to apply this order.`,
    )
  }

  return (
    <fieldset disabled={controller.personalMutationPending}>
      <legend className="text-sm font-semibold">Navigation items</legend>
      <p id={instructionsId} className="mt-1 text-xs text-slate dark:text-slate-400">
        Only items your organization lets you personalize appear here. Fixed destinations and containers stay in place. Main order applies on desktop; mobile main order follows organization defaults. Settings use this order on every screen size. Drag within a named group. Use the earlier and later buttons for keyboard or touch.
      </p>
      <p role="status" aria-live="polite" aria-atomic="true" className="mt-1 min-h-4 text-xs text-slate dark:text-slate-400">
        {reorderStatus}
      </p>
      <div className="mt-2 divide-y divide-slate/15 rounded-lg border border-slate/20 dark:divide-white/10 dark:border-white/10">
        {ordered.map(([moduleId, preference]) => {
          const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)
          const resolved = resolvedById.get(moduleId)
          if (!definition || !resolved) return null
          const roleAllowed = resolved.roleAllowed
          const available = isPersonalModuleAvailable(resolved)
          const Icon = definition.icon
          const displayLabel = workspaceModuleDisplayLabel(definition)
          const groupId = workspaceNavigationGroupPresentation(definition).id
          const siblingItems = ordered.filter(
            ([id]) => {
              const candidate = TRUSTED_WORKSPACE_MODULE_BY_ID.get(id)
              return candidate &&
                isPersonalModuleAvailable(resolvedById.get(id)) &&
                candidate.parentId === definition.parentId &&
                workspaceNavigationGroupPresentation(candidate).id === groupId
            },
          )
          const siblingIndex = siblingItems.findIndex(([id]) => id === moduleId)
          const reorderDisabled = controller.personalMutationPending ||
            siblingItems.length < 2 ||
            !available
          const keyboardGrabbed = keyboardGrabbedModuleId === moduleId
          return (
            <div
              key={moduleId}
              data-navigation-reorder-item={moduleId}
              className={`flex flex-wrap items-center gap-2 px-2 py-2 transition ${
                draggedModuleId === moduleId ? 'opacity-50' : ''
              } ${dropTargetModuleId === moduleId ? 'bg-cyan/10 ring-1 ring-inset ring-cyan/40' : ''}`}
              onDragOver={(event) => {
                if (!available || !draggedModuleId || draggedModuleId === moduleId) return
                const draggedDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(draggedModuleId)
                if (
                  !draggedDefinition ||
                  draggedDefinition.parentId !== definition.parentId ||
                  workspaceNavigationGroupPresentation(draggedDefinition).id !== groupId
                ) {
                  event.dataTransfer.dropEffect = 'none'
                  setDropTargetModuleId(null)
                  return
                }
                event.preventDefault()
                event.dataTransfer.dropEffect = 'move'
                setDropTargetModuleId(moduleId)
              }}
              onDrop={(event) => {
                if (!available) return
                event.preventDefault()
                const transferredId = event.dataTransfer.getData('text/plain')
                const sourceId = draggedModuleId ?? (
                  isTrustedWorkspaceModuleId(transferredId) ? transferredId : null
                )
                dropHandled.current = true
                setDraggedModuleId(null)
                setDropTargetModuleId(null)
                if (sourceId) dropModule(sourceId, moduleId)
              }}
            >
              <NavigationDragHandle
                active={keyboardGrabbed}
                count={siblingItems.length}
                describedBy={instructionsId}
                disabled={reorderDisabled}
                label={displayLabel}
                position={siblingIndex + 1}
                onToggle={() => {
                  if (keyboardGrabbed) {
                    setKeyboardGrabbedModuleId(null)
                    setReorderStatus(
                      `Finished reordering ${displayLabel}. Save navigation preferences to apply this order.`,
                    )
                  } else {
                    setKeyboardGrabbedModuleId(moduleId)
                    setReorderStatus(
                      `Picked up ${displayLabel}, position ${siblingIndex + 1} of ${siblingItems.length}. Use the Up and Down arrow keys, then press Enter to finish or Escape to stop reordering.`,
                    )
                  }
                }}
                onStop={() => {
                  setKeyboardGrabbedModuleId(null)
                  setReorderStatus(`Stopped reordering ${displayLabel}.`)
                }}
                onMove={(direction) => moveModule(moduleId, direction)}
                onDragStart={(event) => {
                  dropHandled.current = false
                  setKeyboardGrabbedModuleId(null)
                  setDraggedModuleId(moduleId)
                  event.dataTransfer.effectAllowed = 'move'
                  event.dataTransfer.setData('text/plain', moduleId)
                  setReorderStatus(
                    `Dragging ${displayLabel}. Drop it on another item in ${workspaceModuleReorderGroupLabel(definition)}.`,
                  )
                }}
                onDragEnd={() => {
                  if (!dropHandled.current) {
                    setReorderStatus(`Stopped reordering ${displayLabel}.`)
                  }
                  dropHandled.current = false
                  setDraggedModuleId(null)
                  setDropTargetModuleId(null)
                }}
              />
              <Icon className="h-4 w-4 shrink-0 text-cyan" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-ink dark:text-slate-100">{displayLabel}</p>
                <p className="text-xs text-slate dark:text-slate-400">{workspaceModuleSectionLabel(definition)}</p>
                {!roleAllowed ? (
                  <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                    {definition.requiredBaseRoles?.map(formatSettingsRoleLabel).join(' or ')} base role required.
                  </p>
                ) : !available ? (
                  <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                    Unavailable under current policy, permissions, or feature settings.
                  </p>
                ) : null}
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
                      const landingStillAvailable = resolvedPersonalLandingOptions(
                        controller,
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
                <NavigationOrderButton
                  label={`Move ${displayLabel} earlier`}
                  disabled={siblingIndex === 0 || reorderDisabled}
                  onClick={() => moveModule(moduleId, -1)}
                >
                  <ArrowUp className="h-4 w-4" aria-hidden="true" />
                </NavigationOrderButton>
                <NavigationOrderButton
                  label={`Move ${displayLabel} later`}
                  disabled={siblingIndex === siblingItems.length - 1 || reorderDisabled}
                  onClick={() => moveModule(moduleId, 1)}
                >
                  <ArrowDown className="h-4 w-4" aria-hidden="true" />
                </NavigationOrderButton>
              </div>
            </div>
          )
        })}
      </div>
    </fieldset>
  )
}

function PersonalNavigationStructurePreview({
  controller,
}: {
  controller: WorkspaceSettingsController
}) {
  const items = personalNavigationPreview(
    controller.workspace.model.modules,
    controller.personalDraft!,
  )

  return (
    <section
      className="rounded-lg border border-slate/20 bg-slate/5 p-3 dark:border-white/10 dark:bg-white/[0.03]"
      aria-labelledby="personal-navigation-preview-heading"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="personal-navigation-preview-heading" className="text-sm font-semibold">
          Navigation preview
        </h3>
        <span className="rounded border border-slate/20 px-2 py-0.5 text-xs text-slate dark:border-white/10 dark:text-slate-300">
          Draft
        </span>
      </div>
      <p className="mt-1 text-xs text-slate dark:text-slate-400">
        Every destination that will remain visible is shown here. Fixed destinations and containers are included for context. Main navigation reflects desktop order.
      </p>
      <div className="mt-2 grid gap-x-4 gap-y-2 md:grid-cols-2 xl:grid-cols-3">
        {WORKSPACE_NAVIGATION_GROUPS.map((group) => {
          const groupItems = items.filter(
            (item) => workspaceNavigationGroupPresentation(item.module).id === group.id,
          )
          if (groupItems.length === 0) return null
          return (
            <section
              key={group.id}
              aria-labelledby={`personal-navigation-preview-${group.id}`}
              className={group.id === 'settings.integrations'
                ? 'border-l border-slate/20 pl-3 dark:border-white/10'
                : undefined}
            >
              <h4
                id={`personal-navigation-preview-${group.id}`}
                className="text-xs font-semibold text-slate dark:text-slate-300"
              >
                {group.label}
              </h4>
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {groupItems.map(({ module, fixed }) => {
                  const Icon = module.icon
                  return (
                    <li
                      key={module.id}
                      data-navigation-preview-module={module.id}
                      className="inline-flex min-w-0 items-center gap-1.5 rounded border border-slate/20 bg-white px-2 py-1 text-xs dark:border-white/10 dark:bg-[#072019]"
                    >
                      <Icon className="h-3.5 w-3.5 shrink-0 text-cyan" aria-hidden="true" />
                      <span>{workspaceModuleDisplayLabel(module)}</span>
                      {fixed && (
                        <span className="rounded bg-slate/10 px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate dark:bg-white/10 dark:text-slate-300">
                          {module.isContainer ? 'Fixed container' : 'Fixed'}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </section>
          )
        })}
      </div>
    </section>
  )
}

function PersonalLandingControl({ controller }: { controller: WorkspaceSettingsController }) {
  const draft = controller.personalDraft!
  const options = resolvedPersonalLandingOptions(controller, draft)
  const trustedCurrent = draft.landingModuleId === null || options.some((module) => module.id === draft.landingModuleId)

  return (
    <label className="block max-w-xl text-sm font-semibold">
      Start page
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
          <option value={draft.landingModuleId}>Unavailable in this version (kept)</option>
        )}
        {WORKSPACE_NAVIGATION_GROUPS.map((group) => {
          const groupOptions = options.filter(
            (module) => workspaceNavigationGroupPresentation(module).id === group.id,
          )
          return groupOptions.length > 0 ? (
            <optgroup key={group.id} label={group.label}>
              {groupOptions.map((module) => (
                <option key={module.id} value={module.id}>
                  {workspaceModuleDisplayLabel(module)}
                </option>
              ))}
            </optgroup>
          ) : null
        })}
      </select>
      <span className="mt-1 block text-xs font-normal text-slate dark:text-slate-400">
        Used after sign-in and whenever ThreatLens opens the workspace start route.
      </span>
    </label>
  )
}

function PersonalDashboardControls({ controller }: { controller: WorkspaceSettingsController }) {
  const draft = controller.personalDraft!
  return (
    <fieldset disabled={controller.personalMutationPending}>
      <legend className="text-sm font-semibold">Initial dashboard panels</legend>
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

function workspaceModuleSectionLabel(module: TrustedWorkspaceModule): string {
  return workspaceNavigationGroupPresentation(module).label
}

function workspaceModuleReorderGroupLabel(module: TrustedWorkspaceModule): string {
  return workspaceNavigationGroupPresentation(module).label
}

function isPersonalModuleAvailable(
  module: ResolvedWorkspaceModule | undefined,
): boolean {
  return Boolean(
    module?.roleAllowed &&
    module.policyVisible &&
    module.permissionAllowed &&
    module.featureAvailable &&
    !module.reasons.includes('account_ineligible'),
  )
}

function resolvedPersonalLandingOptions(
  controller: WorkspaceSettingsController,
  draft: NonNullable<WorkspaceSettingsController['personalDraft']>,
) {
  const availableIds = new Set(
    controller.workspace.model.modules
      .filter(isPersonalModuleAvailable)
      .map((module) => module.id),
  )
  return personalLandingOptions(
    controller.workspace.effective!,
    draft,
    controller.workspace.userContext.role,
  ).filter((module) => availableIds.has(module.id))
}
