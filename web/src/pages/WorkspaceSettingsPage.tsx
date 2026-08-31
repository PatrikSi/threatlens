import { RefreshCw, ShieldCheck } from 'lucide-react'

import { ConfirmDialog } from '../components/ConfirmDialog'
import { WorkspacePersonalizationPanel } from './WorkspacePersonalizationPanel'
import { WorkspaceRolePolicyPanel } from './WorkspaceRolePolicyPanel'
import { WorkspaceCompatibilityWarnings } from './WorkspaceCompatibilityWarnings'
import { useWorkspaceSettingsController } from './useWorkspaceSettingsController'

export function WorkspaceSettingsPage() {
  const controller = useWorkspaceSettingsController()
  const workspaceWarnings = [
    ...controller.workspace.model.warnings,
    ...(controller.workspace.preferences?.warnings ?? []),
    ...(controller.workspace.preferences?.unknown_module_ids ?? []).map((id) => `unknown_preference_module:${id}`),
    ...(controller.workspace.preferences?.unknown_dashboard_panel_ids ?? []).map(
      (id) => `unknown_dashboard_panel:${id}`,
    ),
  ]

  return (
    <div className="space-y-4">
      <header className="tl-surface rounded-xl p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-cyan" aria-hidden="true" />
              <h1 className="font-display text-xl">Workspace</h1>
            </div>
            <p className="mt-1 max-w-3xl text-sm text-slate dark:text-slate-300">
              Choose your working modules and landing view. Administrators can also define the organization defaults for each built-in role.
            </p>
          </div>
          <button
            type="button"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0 dark:border-cyan-900/40"
            disabled={
              controller.isRefreshing ||
              controller.personalMutationPending ||
              controller.roleMutationPending
            }
            title={controller.hasUnsavedChanges ? 'Discard unsaved workspace changes and load the latest revisions.' : undefined}
            onClick={() => {
              if (controller.hasUnsavedChanges) {
                controller.setDiscardReloadRequested(true)
                return
              }
              void controller.refresh()
            }}
          >
            <RefreshCw className={`h-4 w-4 ${controller.isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
            {controller.isRefreshing
              ? 'Refreshing...'
              : controller.hasUnsavedChanges
                ? 'Discard and reload'
                : 'Refresh'}
          </button>
        </div>
        {controller.workspaceError && (
          <div role="alert" className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
            <p>{controller.workspaceError}</p>
            <p className="mt-1">Trusted local defaults remain active while the server workspace is unavailable.</p>
          </div>
        )}
        <WorkspaceCompatibilityWarnings warnings={workspaceWarnings} />
      </header>

      <WorkspacePersonalizationPanel controller={controller} />
      {controller.canManagePolicies && <WorkspaceRolePolicyPanel controller={controller} />}

      <ConfirmDialog
        open={controller.discardReloadRequested}
        title="Discard workspace changes and reload?"
        description="All unsaved personal and role-policy edits on this page will be discarded, then the latest server revisions will be loaded."
        confirmLabel="Discard and reload"
        confirmingLabel="Reloading..."
        confirmTone="danger"
        isConfirming={controller.isRefreshing}
        onCancel={() => controller.setDiscardReloadRequested(false)}
        onConfirm={() => void controller.discardAndReload()}
      />
      <ConfirmDialog
        open={controller.requestedRole !== null}
        title="Discard role policy changes?"
        description={`Switching to ${controller.requestedRole ?? 'another'} role will discard the unsaved changes for ${controller.selectedRole}.`}
        confirmLabel="Discard and switch"
        confirmingLabel="Switching..."
        confirmTone="danger"
        isConfirming={false}
        onCancel={controller.cancelRoleSelection}
        onConfirm={controller.confirmRoleSelection}
      />
      <ConfirmDialog
        open={controller.resetPersonalRequested}
        title="Reset personal workspace?"
        description="Your module visibility, order, landing view, and dashboard-panel choices will return to the organization defaults."
        confirmLabel="Reset personal workspace"
        confirmingLabel="Resetting..."
        confirmTone="primary"
        isConfirming={controller.workspace.isResettingPreferences}
        onCancel={() => controller.setResetPersonalRequested(false)}
        onConfirm={() => void controller.resetPersonal()}
      />
      <ConfirmDialog
        open={controller.resetRoleRequested}
        title={`Reset ${controller.selectedRole} role policy?`}
        description="This changes the organization defaults for every user with this built-in role. Personal choices are retained where the reset policy still permits them."
        confirmLabel="Reset role defaults"
        confirmingLabel="Resetting..."
        isConfirming={controller.resetRolePolicy.isPending}
        onCancel={() => controller.setResetRoleRequested(false)}
        onConfirm={() => controller.resetRolePolicy.mutate()}
      />
    </div>
  )
}
