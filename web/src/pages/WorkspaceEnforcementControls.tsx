import { resolveApiErrorMessage } from '../api/errors'
import type { WorkspaceSettingsController } from './useWorkspaceSettingsController'

export function WorkspaceEnforcementControls({ controller }: { controller: WorkspaceSettingsController }) {
  const draft = controller.roleDraft!
  const disabled = !controller.canManagePolicies || controller.roleMutationPending
  const views = controller.templateViewsQuery
  const selectClassName = 'mt-1 w-full rounded border bg-white p-2 dark:bg-[#072019]'

  return (
    <fieldset disabled={disabled} className="space-y-3 rounded-lg border border-slate/20 p-3 dark:border-white/10">
      <legend className="px-1 font-semibold">Organization defaults and enforcement</legend>
      <p className="text-sm">
        Fixed navigation items already enforce visibility and order. Enforce the start page or dashboard
        arrangement below when everyone in this role must use the organization layout.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm font-semibold">
          Start page policy
          <select
            disabled={disabled}
            className={selectClassName}
            value={draft.landingMode ?? 'default'}
            onChange={(event) => controller.setRoleDraft((current) => current
              ? { ...current, landingMode: event.target.value as 'default' | 'enforced' } : current)}
          >
            <option value="default">Default — users may choose another start page</option>
            <option value="enforced">Enforced — use the organization start page</option>
          </select>
        </label>
        <label className="text-sm font-semibold">
          Dashboard arrangement policy
          <select
            disabled={disabled}
            className={selectClassName}
            value={draft.dashboardMode ?? 'default'}
            onChange={(event) => controller.setRoleDraft((current) => current
              ? { ...current, dashboardMode: event.target.value as 'default' | 'enforced' } : current)}
          >
            <option value="default">Default — seed new dashboards only</option>
            <option value="enforced">Enforced — lock the organization arrangement</option>
          </select>
        </label>
      </div>
      <label className="block text-sm font-semibold">
        Copy a saved view as the organization template
        <select
          value=""
          className={selectClassName}
          disabled={disabled || views?.isLoading || views?.isError}
          onChange={(event) => {
            const view = views?.data?.find((entry) => entry.id === event.target.value)
            if (view) controller.setRoleDraft((current) => current
              ? { ...current, dashboardView: structuredClone(view.query_json) } : current)
          }}
        >
          <option value="">Choose a saved view to copy</option>
          {views?.data?.map((view) => <option key={view.id} value={view.id}>{view.name}</option>)}
        </select>
      </label>
      {views?.isError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {resolveApiErrorMessage(views.error, 'Saved view templates could not be loaded')}{' '}
          <button type="button" className="underline" onClick={() => { void views.refetch() }}>Retry saved views</button>
        </p>
      )}
      {draft.dashboardView ? (
        <div className="text-sm">
          <p>Template snapshot: {draft.dashboardView.windows.length} panels. Later changes to the saved view will not change this policy.</p>
          <ul className="mt-1 list-inside list-disc">
            {draft.dashboardView.windows.map((window) => <li key={window.id}>{window.title} ({window.type.replaceAll('_', ' ')})</li>)}
          </ul>
          <button
            type="button"
            disabled={disabled}
            className="mt-2 underline"
            onClick={() => controller.setRoleDraft((current) => current ? { ...current, dashboardView: null } : current)}
          >Remove template; use initial panel choices</button>
        </div>
      ) : <p className="text-sm">No template selected. Initial panel choices determine the arrangement.</p>}
      <p className="text-xs text-slate dark:text-slate-300">
        Saving publishes the panel titles, layout and filter defaults to this role. Scratch notes,
        selected feeds, alert rules and individual briefs are removed. Existing personal layouts
        remain stored separately while enforcement is active; article notes remain editable.
      </p>
    </fieldset>
  )
}
