import { ArrowDown, ArrowUp, RefreshCw, RotateCcw, Save } from 'lucide-react'
import { useRef, useState } from 'react'

import {
  TRUSTED_DASHBOARD_PANELS,
  TRUSTED_WORKSPACE_MODULE_BY_ID,
  isTopNavigationModule,
  type TrustedWorkspaceModule,
  type TrustedWorkspaceModuleId,
} from '../workspace/moduleRegistry'
import {
  WORKSPACE_NAVIGATION_GROUPS,
  formatSettingsRoleLabel,
  workspaceModuleDisplayLabel,
  workspaceNavigationGroupOrder,
  workspaceNavigationGroupPresentation,
} from '../workspace/modulePresentation'
import { isWorkspaceModuleRoleAllowed } from '../workspace/workspaceModel'
import type { WorkspaceSettingsController } from './useWorkspaceSettingsController'
import {
  reorderRolePolicyModule,
  rolePolicyPreview,
  toggleStringValue,
  updateRolePolicyModule,
} from './workspaceSettingsModel'
import { WorkspaceCompatibilityWarnings } from './WorkspaceCompatibilityWarnings'
import {
  NavigationDragHandle,
  NavigationOrderButton,
} from './WorkspaceNavigationReorderControls'

export function WorkspaceRolePolicyPanel({ controller }: { controller: WorkspaceSettingsController }) {
  const policy = controller.selectedPolicy
  const draft = controller.roleDraft
  const selectedRoleLabel = formatSettingsRoleLabel(controller.selectedRole)

  return (
    <section className="tl-surface overflow-hidden rounded-xl" aria-labelledby="role-workspace-heading">
      <header className="border-b border-slate/20 px-4 py-3.5 dark:border-white/10">
        <h2 id="role-workspace-heading" className="font-display text-lg">Workspace navigation by role</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          {controller.canManagePolicies ? 'Set' : 'Review'} top-navigation and Settings-sidebar defaults for each built-in role. These choices control presentation only and never grant permissions.
        </p>
        <div className="mt-3 inline-flex max-w-full overflow-x-auto rounded border border-slate/20 p-1 dark:border-white/10" role="group" aria-label="Built-in role">
          {controller.roles.map((role) => (
            <button
              key={role}
              type="button"
              className={`min-h-11 rounded px-3 py-1.5 text-sm font-semibold disabled:opacity-50 sm:min-h-0 ${
                role === controller.selectedRole
                  ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                  : 'text-slate dark:text-slate-200'
              }`}
              aria-pressed={role === controller.selectedRole}
              disabled={controller.roleMutationPending}
              onClick={() => controller.selectRole(role)}
            >
              {formatSettingsRoleLabel(role)}
            </button>
          ))}
        </div>
      </header>

      {controller.rolePoliciesLoading && <p className="px-4 py-4 text-sm text-slate dark:text-slate-300">Loading navigation defaults...</p>}
      {controller.roleError && (
        <div role="alert" className="m-3 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
          <p>{controller.roleError}</p>
          {controller.roleRevisionConflict && (
            <button
              type="button"
              className="mt-2 inline-flex min-h-11 items-center justify-center gap-2 rounded border border-red-400/50 px-3 py-2 font-semibold disabled:opacity-60 sm:min-h-0"
              disabled={controller.roleMutationPending}
              onClick={() => void controller.discardAndReloadRole()}
            >
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              Discard default changes and reload
            </button>
          )}
        </div>
      )}
      {policy && draft && (
        <div className="space-y-4 px-4 py-3.5">
          {!controller.canManagePolicies && (
            <p className="rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm text-slate dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300">
              Read-only organization policy. Changes require durable workspace-management permission.
            </p>
          )}
          <WorkspaceCompatibilityWarnings warnings={controller.selectedPolicyWarnings} />
          <RoleModuleEditor
            key={`${controller.selectedRole}:${controller.canManagePolicies ? 'edit' : 'read'}`}
            controller={controller}
            scope="top"
          />
          <details className="rounded-lg border border-slate/20 bg-slate/5 dark:border-white/10 dark:bg-white/[0.03]">
            <summary className="cursor-pointer px-3 py-2.5 text-sm font-semibold text-ink dark:text-slate-100">
              Settings sidebar defaults
              <span className="ml-2 text-xs font-normal text-slate dark:text-slate-400">
                Separate from the top navigation
              </span>
            </summary>
            <div className="border-t border-slate/15 px-3 py-3 dark:border-white/10">
              <RoleModuleEditor
                key={`${controller.selectedRole}:${controller.canManagePolicies ? 'edit' : 'read'}:settings`}
                controller={controller}
                scope="settings"
              />
            </div>
          </details>
          <RolePolicyControls controller={controller} />
          <RolePolicyPreview controller={controller} />

          {controller.roleValidation && (
            <p role="alert" className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
              {controller.roleValidation}
            </p>
          )}

          {controller.roleFeedback && (
            <p role="status" className="rounded border border-emerald-300/60 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100">
              {controller.roleFeedback}
            </p>
          )}
          {controller.canManagePolicies && (
            <div className="flex flex-col-reverse gap-2 border-t border-slate/15 pt-3 sm:flex-row sm:justify-end dark:border-white/10">
              <button
                type="button"
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0 dark:border-cyan-900/40"
                disabled={controller.roleMutationPending}
                onClick={() => controller.setResetRoleRequested(true)}
              >
                <RotateCcw className="h-4 w-4" aria-hidden="true" />
                Reset {selectedRoleLabel} defaults
              </button>
              <button
                type="button"
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
                disabled={!controller.roleDirty || Boolean(controller.roleValidation) || controller.roleMutationPending}
                onClick={() => controller.updateRolePolicy.mutate()}
              >
                <Save className="h-4 w-4" aria-hidden="true" />
                {controller.updateRolePolicy.isPending ? 'Saving...' : 'Save navigation defaults'}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function RoleModuleEditor({
  controller,
  scope,
}: {
  controller: WorkspaceSettingsController
  scope: 'top' | 'settings'
}) {
  const topNavigation = scope === 'top'
  const instructionsId = topNavigation
    ? 'role-navigation-reorder-instructions'
    : 'role-settings-navigation-reorder-instructions'
  const draft = controller.roleDraft!
  const roleLabel = formatSettingsRoleLabel(controller.selectedRole)
  const [draggedModuleId, setDraggedModuleId] = useState<TrustedWorkspaceModuleId | null>(null)
  const [dropTargetModuleId, setDropTargetModuleId] = useState<TrustedWorkspaceModuleId | null>(null)
  const [keyboardGrabbedModuleId, setKeyboardGrabbedModuleId] = useState<TrustedWorkspaceModuleId | null>(null)
  const [reorderStatus, setReorderStatus] = useState(
    controller.canManagePolicies
      ? `Order changes remain unsaved until you save ${roleLabel} navigation defaults.`
      : `Viewing the organization defaults for ${roleLabel}.`,
  )
  const dropHandled = useRef(false)
  const editingDisabled = !controller.canManagePolicies || controller.roleMutationPending
  const modules = [...draft.modules.entries()].filter(([moduleId]) => {
    const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)
    return definition && (topNavigation
      ? isTopNavigationModule(definition)
      : definition.section === 'settings')
  }).sort(([leftId, left], [rightId, right]) => {
    const leftDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(leftId)
    const rightDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(rightId)
    const groupOrder = leftDefinition && rightDefinition
      ? workspaceNavigationGroupOrder(leftDefinition) - workspaceNavigationGroupOrder(rightDefinition)
      : 0
    return groupOrder || left.order - right.order || leftId.localeCompare(rightId)
  })

  function moveModule(moduleId: TrustedWorkspaceModuleId, direction: -1 | 1) {
    if (editingDisabled) return
    const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)
    if (
      !definition ||
      !isWorkspaceModuleRoleAllowed(definition, controller.selectedRole)
    ) return
    const groupId = workspaceNavigationGroupPresentation(definition).id
    const siblings = modules.filter(
      ([id]) => {
        const candidate = TRUSTED_WORKSPACE_MODULE_BY_ID.get(id)
        return candidate &&
          isWorkspaceModuleRoleAllowed(candidate, controller.selectedRole) &&
          candidate.parentId === definition.parentId &&
          workspaceNavigationGroupPresentation(candidate).id === groupId
      },
    )
    const currentIndex = siblings.findIndex(([id]) => id === moduleId)
    const nextIndex = currentIndex + direction
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= siblings.length) return

    controller.setRoleDraft((current) =>
      current
        ? reorderRolePolicyModule(current, moduleId, siblings[nextIndex]![0])
        : current,
    )
    setReorderStatus(
      `Moved ${workspaceModuleDisplayLabel(definition)} to desktop position ${nextIndex + 1} of ${siblings.length} in ${workspaceModuleReorderGroupLabel(definition)}. Save ${roleLabel} navigation defaults to apply this order.`,
    )
  }

  function dropModule(sourceId: TrustedWorkspaceModuleId, targetId: TrustedWorkspaceModuleId) {
    if (editingDisabled) return
    const sourceDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(sourceId)
    const targetDefinition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(targetId)
    if (
      !sourceDefinition ||
      !targetDefinition ||
      !isWorkspaceModuleRoleAllowed(sourceDefinition, controller.selectedRole) ||
      !isWorkspaceModuleRoleAllowed(targetDefinition, controller.selectedRole)
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
    const siblings = modules.filter(([id]) => {
      const candidate = TRUSTED_WORKSPACE_MODULE_BY_ID.get(id)
      return candidate &&
        isWorkspaceModuleRoleAllowed(candidate, controller.selectedRole) &&
        candidate.parentId === sourceDefinition.parentId &&
        workspaceNavigationGroupPresentation(candidate).id === groupId
    })
    const targetIndex = siblings.findIndex(([id]) => id === targetId)
    if (targetIndex < 0 || sourceId === targetId) {
      setReorderStatus(`${workspaceModuleDisplayLabel(sourceDefinition)} stayed in its current position.`)
      return
    }
    controller.setRoleDraft((current) =>
      current ? reorderRolePolicyModule(current, sourceId, targetId) : current,
    )
    setReorderStatus(
      `Moved ${workspaceModuleDisplayLabel(sourceDefinition)} to desktop position ${targetIndex + 1} of ${siblings.length} in ${workspaceModuleReorderGroupLabel(sourceDefinition)}. Save ${roleLabel} navigation defaults to apply this order.`,
    )
  }

  return (
    <fieldset disabled={editingDisabled}>
      <legend className="text-sm font-semibold">
        {topNavigation ? 'Top navigation defaults' : 'Settings sidebar defaults'}
      </legend>
      <p id={instructionsId} className="mt-1 text-xs text-slate dark:text-slate-400">
        {topNavigation
          ? controller.canManagePolicies
            ? 'Only top-navbar items appear here. Settings stays fixed. Drag to change desktop order, or use the earlier and later buttons for keyboard or touch; set mobile order within the fixed primary and secondary tiers.'
            : 'Only top-navbar items appear here. Settings stays fixed. Desktop and mobile orders are shown separately.'
          : controller.canManagePolicies
            ? 'These items appear only in the Settings sidebar. Drag within a Settings group, or use the earlier and later buttons for keyboard or touch. One order applies at every screen size.'
            : 'These items appear only in the Settings sidebar. One order applies at every screen size.'}
      </p>
      <p role="status" aria-live="polite" aria-atomic="true" className="mt-1 min-h-4 text-xs text-slate dark:text-slate-400">
        {reorderStatus}
      </p>
      <div className="mt-2 rounded-lg border border-slate/20 dark:border-white/10 sm:overflow-x-auto">
        <table className="w-full text-left text-sm sm:min-w-[760px]">
          <thead className="hidden bg-slate/5 text-xs text-slate dark:bg-white/[0.04] dark:text-slate-300 sm:table-header-group">
            <tr>
              <th scope="col" className="px-3 py-2 font-semibold">
                {topNavigation ? 'Top navigation item' : 'Settings sidebar item'}
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">Shown by default</th>
              <th scope="col" className="px-3 py-2 font-semibold">Users can customize</th>
              <th scope="col" className="px-3 py-2 font-semibold">
                {topNavigation ? 'Desktop order' : 'Order in group'}
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                {topNavigation ? 'Mobile order in tier' : 'Responsive order'}
              </th>
            </tr>
          </thead>
          <tbody className="block divide-y divide-slate/15 dark:divide-white/10 sm:table-row-group">
            {modules.map(([moduleId, module]) => {
              const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)
              if (!definition?.policyManaged) return null
              const Icon = definition.icon
              const displayLabel = workspaceModuleDisplayLabel(definition)
              const roleAllowed = isWorkspaceModuleRoleAllowed(
                definition,
                controller.selectedRole,
              )
              const groupId = workspaceNavigationGroupPresentation(definition).id
              const siblingItems = modules.filter(
                ([id]) => {
                  const candidate = TRUSTED_WORKSPACE_MODULE_BY_ID.get(id)
                  return candidate &&
                    isWorkspaceModuleRoleAllowed(candidate, controller.selectedRole) &&
                    candidate.parentId === definition.parentId &&
                    workspaceNavigationGroupPresentation(candidate).id === groupId
                },
              )
              const siblingIndex = siblingItems.findIndex(([id]) => id === moduleId)
              const reorderDisabled = editingDisabled ||
                siblingItems.length < 2 ||
                !roleAllowed
              const keyboardGrabbed = keyboardGrabbedModuleId === moduleId
              return (
                <tr
                  key={moduleId}
                  data-navigation-reorder-item={topNavigation ? moduleId : undefined}
                  data-settings-navigation-reorder-item={topNavigation ? undefined : moduleId}
                  className={`grid grid-cols-2 gap-x-3 gap-y-2 px-2 py-2 transition sm:table-row sm:p-0 ${
                    draggedModuleId === moduleId ? 'opacity-50' : ''
                  } ${dropTargetModuleId === moduleId ? 'bg-cyan/10 ring-1 ring-inset ring-cyan/40' : ''}`}
                  onDragOver={(event) => {
                    if (
                      editingDisabled ||
                      !roleAllowed ||
                      !draggedModuleId ||
                      draggedModuleId === moduleId
                    ) return
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
                    if (editingDisabled || !roleAllowed) return
                    event.preventDefault()
                    const transferredId = event.dataTransfer.getData('text/plain')
                    const sourceId = draggedModuleId ?? (
                      isTrustedRoleModuleId(transferredId, draft.modules) ? transferredId : null
                    )
                    dropHandled.current = true
                    setDraggedModuleId(null)
                    setDropTargetModuleId(null)
                    if (sourceId) dropModule(sourceId, moduleId)
                  }}
                >
                  <td className="col-span-2 p-0 sm:table-cell sm:px-2 sm:py-2">
                    <div className="flex items-center gap-1.5">
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
                              `Finished reordering ${displayLabel}. Save ${roleLabel} navigation defaults to apply this order.`,
                            )
                          } else {
                            setKeyboardGrabbedModuleId(moduleId)
                            setReorderStatus(
                              `Picked up ${displayLabel}, desktop position ${siblingIndex + 1} of ${siblingItems.length}. Use the Up and Down arrow keys, then press Enter to finish or Escape to stop reordering.`,
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
                      <div className="min-w-0">
                        <p className="font-semibold text-ink dark:text-slate-100">{displayLabel}</p>
                        <p className="text-xs text-slate dark:text-slate-400">{workspaceModuleSectionLabel(definition)}</p>
                        {!roleAllowed && (
                          <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-300">
                            Administrator base role required
                          </p>
                        )}
                      </div>
                      <div className="ml-auto flex shrink-0 gap-1">
                        <NavigationOrderButton
                          label={`Move ${displayLabel} earlier for ${roleLabel}`}
                          disabled={siblingIndex === 0 || reorderDisabled}
                          onClick={() => moveModule(moduleId, -1)}
                        >
                          <ArrowUp className="h-4 w-4" aria-hidden="true" />
                        </NavigationOrderButton>
                        <NavigationOrderButton
                          label={`Move ${displayLabel} later for ${roleLabel}`}
                          disabled={siblingIndex === siblingItems.length - 1 || reorderDisabled}
                          onClick={() => moveModule(moduleId, 1)}
                        >
                          <ArrowDown className="h-4 w-4" aria-hidden="true" />
                        </NavigationOrderButton>
                      </div>
                    </div>
                  </td>
                  <td className="flex min-w-0 flex-col gap-1 p-0 sm:table-cell sm:px-2 sm:py-2">
                    <span className="text-xs font-semibold text-slate dark:text-slate-300 sm:hidden">Shown by default</span>
                    <label className="inline-flex min-h-11 items-center gap-2 sm:min-h-0">
                      <input
                        type="checkbox"
                        className="h-5 w-5 sm:h-auto sm:w-auto"
                        aria-label={`Show ${displayLabel} for ${roleLabel}`}
                        checked={module.visible}
                        disabled={editingDisabled || !roleAllowed}
                        onChange={(event) => updateModule(controller, moduleId, { visible: event.target.checked })}
                      />
                      <span className="text-sm sm:hidden">Show</span>
                    </label>
                  </td>
                  <td className="flex min-w-0 flex-col gap-1 p-0 sm:table-cell sm:px-2 sm:py-2">
                    <span className="text-xs font-semibold text-slate dark:text-slate-300 sm:hidden">Users can customize</span>
                    <label className="inline-flex min-h-11 items-center gap-2 sm:min-h-0">
                      <input
                        type="checkbox"
                        className="h-5 w-5 sm:h-auto sm:w-auto"
                        aria-label={`Allow users to customize ${displayLabel}`}
                        checked={module.optional}
                        disabled={editingDisabled || !roleAllowed}
                        onChange={(event) => updateModule(controller, moduleId, { optional: event.target.checked })}
                      />
                      <span className="text-sm sm:hidden">Allow</span>
                    </label>
                  </td>
                  <td className="flex min-w-0 flex-col gap-1 p-0 sm:table-cell sm:px-2 sm:py-2">
                    <span className="text-xs font-semibold text-slate dark:text-slate-300 sm:hidden">
                      {topNavigation ? 'Desktop order' : 'Order in group'}
                    </span>
                    <PolicyNumberInput
                      label={`Desktop order in ${workspaceNavigationGroupPresentation(definition).label} for ${displayLabel}`}
                      value={module.order}
                      disabled={editingDisabled || !roleAllowed}
                      onChange={(order) => updateModule(controller, moduleId, { order })}
                    />
                  </td>
                  <td className="flex min-w-0 flex-col gap-1 p-0 sm:table-cell sm:px-2 sm:py-2">
                    <span className="text-xs font-semibold text-slate dark:text-slate-300 sm:hidden">
                      {topNavigation ? 'Mobile order in tier' : 'Responsive order'}
                    </span>
                    {definition.section === 'primary' ? (
                      <div className="space-y-0.5">
                        <PolicyNumberInput
                          label={`Mobile order in ${definition.mobileBehavior} tier for ${displayLabel}`}
                          value={module.mobile_priority}
                          disabled={editingDisabled || !roleAllowed}
                          onChange={(mobilePriority) => updateModule(controller, moduleId, { mobile_priority: mobilePriority })}
                        />
                        <span className="block text-[11px] capitalize text-slate dark:text-slate-400">
                          {definition.mobileBehavior} tier
                        </span>
                      </div>
                    ) : (
                      <span
                        className="inline-flex min-h-11 items-center text-xs text-slate dark:text-slate-400 sm:min-h-0"
                        aria-label={`Mobile order for ${displayLabel} uses desktop group order`}
                      >
                        Same as desktop
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </fieldset>
  )
}

function PolicyNumberInput({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string
  value: number
  disabled: boolean
  onChange: (value: number) => void
}) {
  return (
    <input
      type="number"
      min={0}
      max={10_000}
      step={1}
      className="min-h-11 w-full min-w-0 rounded border border-slate/30 bg-white px-2 py-1.5 sm:min-h-0 sm:w-24 dark:border-cyan-900/40 dark:bg-[#072019]"
      aria-label={label}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(clampOrder(event.target.valueAsNumber))}
    />
  )
}

function updateModule(
  controller: WorkspaceSettingsController,
  moduleId: TrustedWorkspaceModuleId,
  patch: Parameters<typeof updateRolePolicyModule>[2],
) {
  controller.setRoleDraft((current) => current ? updateRolePolicyModule(current, moduleId, patch) : current)
}

function RolePolicyControls({ controller }: { controller: WorkspaceSettingsController }) {
  const draft = controller.roleDraft!
  const editingDisabled = !controller.canManagePolicies || controller.roleMutationPending
  const preview = rolePolicyPreview(draft, controller.selectedRole)
  const landingOptions = [...preview.primary, ...preview.settings].filter(
    (module) => module.policyManaged && module.landingEligible,
  )
  const trustedLanding = landingOptions.some((module) => module.id === draft.landingModuleId)
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <label className="block text-sm font-semibold">
        Default start page
        <select
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          value={draft.landingModuleId}
          disabled={editingDisabled}
          onChange={(event) => controller.setRoleDraft((current) => current ? { ...current, landingModuleId: event.target.value } : current)}
        >
          {!trustedLanding && <option value={draft.landingModuleId}>Unavailable in this version (kept)</option>}
          {WORKSPACE_NAVIGATION_GROUPS.map((group) => {
            const groupOptions = landingOptions.filter(
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
          Available Settings pages can be selected independently of the top navigation.
        </span>
      </label>
      <fieldset disabled={editingDisabled}>
        <legend className="text-sm font-semibold">Initial dashboard panels</legend>
        <p className="mt-1 text-xs font-normal text-slate dark:text-slate-400">
          These panels seed dashboards for role members who do not have a saved local layout.
        </p>
        <div className="mt-1 grid gap-2 sm:grid-cols-2">
          {TRUSTED_DASHBOARD_PANELS.map((panel) => {
            const Icon = panel.icon
            return (
              <label key={panel.id} className="flex min-h-11 items-center gap-2 rounded border border-slate/20 px-3 py-2 text-sm dark:border-white/10">
                <input
                  type="checkbox"
                  checked={draft.dashboardPanelIds.includes(panel.id)}
                  disabled={
                    editingDisabled ||
                    (draft.dashboardPanelIds.length === 1 && draft.dashboardPanelIds.includes(panel.id))
                  }
                  onChange={(event) => controller.setRoleDraft((current) => current ? {
                    ...current,
                    dashboardPanelIds: toggleStringValue(current.dashboardPanelIds, panel.id, event.target.checked),
                  } : current)}
                />
                <Icon className="h-4 w-4 text-cyan" aria-hidden="true" />
                {panel.label}
              </label>
            )
          })}
        </div>
      </fieldset>
    </div>
  )
}

function RolePolicyPreview({ controller }: { controller: WorkspaceSettingsController }) {
  const preview = rolePolicyPreview(controller.roleDraft!, controller.selectedRole)
  const roleLabel = formatSettingsRoleLabel(controller.selectedRole)
  return (
    <section className="rounded border border-slate/20 bg-slate/5 p-3 dark:border-white/10 dark:bg-white/[0.03]" aria-label={`${roleLabel} top navigation preview`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{roleLabel} top navigation preview</h3>
        <span className="rounded border border-slate/20 px-2 py-0.5 text-xs text-slate dark:border-white/10 dark:text-slate-300">Preview only</span>
      </div>
      <p className="mt-1 text-xs text-slate dark:text-slate-400">
        This preview is inert and matches the top navbar at policy level. User permissions, feature availability, and personal choices can hide more items.
      </p>
      <div className="mt-3 space-y-3">
        <PreviewRow label="Desktop top navigation" modules={preview.primary} />
        <PreviewRow label="Mobile navigation" modules={preview.mobile} />
      </div>
    </section>
  )
}

function PreviewRow({
  label,
  modules,
}: {
  label: string
  modules: ReturnType<typeof rolePolicyPreview>['primary']
}) {
  return (
    <div>
      <p className="text-xs font-semibold text-slate dark:text-slate-300">{label}</p>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {modules.length === 0 && <span className="text-xs text-slate dark:text-slate-400">No visible modules</span>}
        {modules.map((module) => {
          const Icon = module.icon
          return (
            <span
              key={module.id}
              data-navigation-preview-module={module.id}
              className="inline-flex items-center gap-1.5 rounded border border-slate/20 bg-white px-2 py-1 text-xs dark:border-white/10 dark:bg-[#072019]"
            >
              <Icon className="h-3.5 w-3.5 text-cyan" aria-hidden="true" />
              {workspaceModuleDisplayLabel(module)}
              {!module.policyManaged && (
                <span className="rounded bg-slate/10 px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate dark:bg-white/10 dark:text-slate-300">
                  {module.isContainer ? 'Fixed container' : 'Fixed'}
                </span>
              )}
            </span>
          )
        })}
      </div>
    </div>
  )
}

function clampOrder(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(10_000, Math.max(0, Math.round(value)))
}

function workspaceModuleSectionLabel(module: TrustedWorkspaceModule): string {
  if (isTopNavigationModule(module)) return 'Top navigation'
  return workspaceNavigationGroupPresentation(module).label
}

function workspaceModuleReorderGroupLabel(module: TrustedWorkspaceModule): string {
  if (isTopNavigationModule(module)) return 'top navigation'
  return workspaceNavigationGroupPresentation(module).label
}

function isTrustedRoleModuleId(
  value: string,
  modules: ReadonlyMap<TrustedWorkspaceModuleId, unknown>,
): value is TrustedWorkspaceModuleId {
  return modules.has(value as TrustedWorkspaceModuleId)
}
