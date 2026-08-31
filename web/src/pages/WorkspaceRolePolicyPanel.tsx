import { RefreshCw, RotateCcw, Save } from 'lucide-react'

import {
  TRUSTED_DASHBOARD_PANELS,
  TRUSTED_WORKSPACE_MODULE_BY_ID,
  type TrustedWorkspaceModuleId,
} from '../workspace/moduleRegistry'
import type { WorkspaceSettingsController } from './useWorkspaceSettingsController'
import {
  rolePolicyPreview,
  toggleStringValue,
  updateRolePolicyModule,
} from './workspaceSettingsModel'
import { WorkspaceCompatibilityWarnings } from './WorkspaceCompatibilityWarnings'

export function WorkspaceRolePolicyPanel({ controller }: { controller: WorkspaceSettingsController }) {
  const policy = controller.selectedPolicy
  const draft = controller.roleDraft

  return (
    <section className="tl-surface overflow-hidden rounded-xl" aria-labelledby="role-workspace-heading">
      <header className="border-b border-slate/20 px-4 py-4 dark:border-white/10 sm:px-5">
        <h2 id="role-workspace-heading" className="font-display text-lg">Organization role defaults</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          Define module defaults for built-in roles. This policy controls presentation only; IAM permissions remain authoritative.
        </p>
        <div className="mt-3 inline-flex max-w-full overflow-x-auto rounded border border-slate/20 p-1 dark:border-white/10" role="group" aria-label="Workspace role">
          {controller.roles.map((role) => (
            <button
              key={role}
              type="button"
              className={`min-h-11 rounded px-3 py-1.5 text-sm font-semibold capitalize disabled:opacity-50 sm:min-h-0 ${
                role === controller.selectedRole
                  ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                  : 'text-slate dark:text-slate-200'
              }`}
              aria-pressed={role === controller.selectedRole}
              disabled={controller.roleMutationPending}
              onClick={() => controller.selectRole(role)}
            >
              {role}
            </button>
          ))}
        </div>
      </header>

      {controller.rolePoliciesLoading && <p className="px-4 py-6 text-sm text-slate dark:text-slate-300">Loading organization policies...</p>}
      {controller.roleError && (
        <div role="alert" className="m-4 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
          <p>{controller.roleError}</p>
          {controller.roleRevisionConflict && (
            <button
              type="button"
              className="mt-2 inline-flex min-h-11 items-center justify-center gap-2 rounded border border-red-400/50 px-3 py-2 font-semibold disabled:opacity-60 sm:min-h-0"
              disabled={controller.roleMutationPending}
              onClick={() => void controller.discardAndReloadRole()}
            >
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              Discard role changes and reload
            </button>
          )}
        </div>
      )}
      {policy && draft && (
        <div className="space-y-5 px-4 py-4 sm:px-5">
          <WorkspaceCompatibilityWarnings warnings={controller.selectedPolicyWarnings} />
          <RoleModuleEditor controller={controller} />
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
          <div className="flex flex-col-reverse gap-2 border-t border-slate/15 pt-4 sm:flex-row sm:justify-end dark:border-white/10">
            <button
              type="button"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0 dark:border-cyan-900/40"
              disabled={controller.roleMutationPending}
              onClick={() => controller.setResetRoleRequested(true)}
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              Reset role defaults
            </button>
            <button
              type="button"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
              disabled={!controller.roleDirty || Boolean(controller.roleValidation) || controller.roleMutationPending}
              onClick={() => controller.updateRolePolicy.mutate()}
            >
              <Save className="h-4 w-4" aria-hidden="true" />
              {controller.updateRolePolicy.isPending ? 'Saving...' : 'Save role policy'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function RoleModuleEditor({ controller }: { controller: WorkspaceSettingsController }) {
  const draft = controller.roleDraft!
  const modules = [...draft.modules.entries()].sort(([leftId, left], [rightId, right]) => {
    const leftSection = TRUSTED_WORKSPACE_MODULE_BY_ID.get(leftId)?.section ?? 'settings'
    const rightSection = TRUSTED_WORKSPACE_MODULE_BY_ID.get(rightId)?.section ?? 'settings'
    return leftSection.localeCompare(rightSection) || left.order - right.order || leftId.localeCompare(rightId)
  })
  return (
    <fieldset disabled={controller.roleMutationPending}>
      <legend className="text-sm font-semibold">Module policy</legend>
      <div className="mt-3 rounded border border-slate/20 dark:border-white/10 sm:overflow-x-auto">
        <table className="w-full text-left text-sm sm:min-w-[760px]">
          <thead className="hidden bg-slate/5 text-xs text-slate dark:bg-white/[0.04] dark:text-slate-300 sm:table-header-group">
            <tr>
              <th className="px-3 py-2 font-semibold">Module</th>
              <th className="px-3 py-2 font-semibold">Visible</th>
              <th className="px-3 py-2 font-semibold">Personal choice</th>
              <th className="px-3 py-2 font-semibold">Desktop order</th>
              <th className="px-3 py-2 font-semibold">Mobile priority</th>
            </tr>
          </thead>
          <tbody className="block divide-y divide-slate/15 dark:divide-white/10 sm:table-row-group">
            {modules.map(([moduleId, module]) => {
              const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)
              if (!definition?.policyManaged) return null
              const Icon = definition.icon
              return (
                <tr key={moduleId} className="grid grid-cols-2 gap-x-4 gap-y-3 px-3 py-3 sm:table-row sm:p-0">
                  <td className="col-span-2 p-0 sm:table-cell sm:px-3 sm:py-2.5">
                    <div className="flex items-start gap-2">
                      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-cyan" aria-hidden="true" />
                      <div className="min-w-0">
                        <p className="font-semibold text-ink dark:text-slate-100">{definition.label}</p>
                        <p className="break-all text-xs text-slate dark:text-slate-400">{moduleId}</p>
                      </div>
                    </div>
                  </td>
                  <td className="flex min-w-0 flex-col gap-1 p-0 sm:table-cell sm:px-3 sm:py-2.5">
                    <span className="text-xs font-semibold text-slate dark:text-slate-300 sm:hidden">Visible</span>
                    <label className="inline-flex min-h-11 items-center gap-2 sm:min-h-0">
                      <input
                        type="checkbox"
                        className="h-5 w-5 sm:h-auto sm:w-auto"
                        aria-label={`Show ${definition.label} for ${controller.selectedRole}`}
                        checked={module.visible}
                        disabled={controller.roleMutationPending}
                        onChange={(event) => updateModule(controller, moduleId, { visible: event.target.checked })}
                      />
                      <span className="text-sm sm:hidden">Show</span>
                    </label>
                  </td>
                  <td className="flex min-w-0 flex-col gap-1 p-0 sm:table-cell sm:px-3 sm:py-2.5">
                    <span className="text-xs font-semibold text-slate dark:text-slate-300 sm:hidden">Personal choice</span>
                    <label className="inline-flex min-h-11 items-center gap-2 sm:min-h-0">
                      <input
                        type="checkbox"
                        className="h-5 w-5 sm:h-auto sm:w-auto"
                        aria-label={`Allow personal choice for ${definition.label}`}
                        checked={module.optional}
                        disabled={controller.roleMutationPending}
                        onChange={(event) => updateModule(controller, moduleId, { optional: event.target.checked })}
                      />
                      <span className="text-sm sm:hidden">Allow</span>
                    </label>
                  </td>
                  <td className="flex min-w-0 flex-col gap-1 p-0 sm:table-cell sm:px-3 sm:py-2.5">
                    <span className="text-xs font-semibold text-slate dark:text-slate-300 sm:hidden">Desktop order</span>
                    <PolicyNumberInput
                      label={`Desktop order for ${definition.label}`}
                      value={module.order}
                      disabled={controller.roleMutationPending}
                      onChange={(order) => updateModule(controller, moduleId, { order })}
                    />
                  </td>
                  <td className="flex min-w-0 flex-col gap-1 p-0 sm:table-cell sm:px-3 sm:py-2.5">
                    <span className="text-xs font-semibold text-slate dark:text-slate-300 sm:hidden">Mobile priority</span>
                    <PolicyNumberInput
                      label={`Mobile priority for ${definition.label}`}
                      value={module.mobile_priority}
                      disabled={controller.roleMutationPending}
                      onChange={(mobilePriority) => updateModule(controller, moduleId, { mobile_priority: mobilePriority })}
                    />
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
  const preview = rolePolicyPreview(draft)
  const landingOptions = [...preview.primary, ...preview.settings].filter((module) => module.policyManaged)
  const trustedLanding = landingOptions.some((module) => module.id === draft.landingModuleId)
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <label className="block text-sm font-semibold">
        Default landing module
        <select
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          value={draft.landingModuleId}
          disabled={controller.roleMutationPending}
          onChange={(event) => controller.setRoleDraft((current) => current ? { ...current, landingModuleId: event.target.value } : current)}
        >
          {!trustedLanding && <option value={draft.landingModuleId}>Unavailable in this frontend (preserved)</option>}
          {landingOptions.map((module) => <option key={module.id} value={module.id}>{module.label}</option>)}
        </select>
      </label>
      <fieldset disabled={controller.roleMutationPending}>
        <legend className="text-sm font-semibold">First-use dashboard defaults</legend>
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
                    controller.roleMutationPending ||
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
  const preview = rolePolicyPreview(controller.roleDraft!)
  return (
    <section className="rounded border border-slate/20 bg-slate/5 p-3 dark:border-white/10 dark:bg-white/[0.03]" aria-label={`${controller.selectedRole} role policy preview`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold capitalize">{controller.selectedRole} navigation preview</h3>
        <span className="rounded border border-slate/20 px-2 py-0.5 text-xs text-slate dark:border-white/10 dark:text-slate-300">Preview only</span>
      </div>
      <p className="mt-1 text-xs text-slate dark:text-slate-400">
        This preview is inert and reflects policy visibility only. User permissions, feature availability, and personal choices can hide more modules.
      </p>
      <div className="mt-3 space-y-3">
        <PreviewRow label="Desktop navigation" modules={preview.primary} />
        <PreviewRow label="Mobile navigation" modules={preview.mobile} />
        <PreviewRow label="Settings navigation" modules={preview.settings} />
      </div>
    </section>
  )
}

function PreviewRow({ label, modules }: { label: string; modules: ReturnType<typeof rolePolicyPreview>['primary'] }) {
  return (
    <div>
      <p className="text-xs font-semibold text-slate dark:text-slate-300">{label}</p>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {modules.length === 0 && <span className="text-xs text-slate dark:text-slate-400">No visible modules</span>}
        {modules.map((module) => {
          const Icon = module.icon
          return (
            <span key={module.id} className="inline-flex items-center gap-1.5 rounded border border-slate/20 bg-white px-2 py-1 text-xs dark:border-white/10 dark:bg-[#072019]">
              <Icon className="h-3.5 w-3.5 text-cyan" aria-hidden="true" />
              {module.label}
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
