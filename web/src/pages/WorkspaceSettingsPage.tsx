import { RefreshCw } from 'lucide-react'

import { ConfirmDialog } from '../components/ConfirmDialog'
import { SettingsPageHeader } from '../components/SettingsPageHeader'
import { formatSettingsRoleLabel } from '../workspace/modulePresentation'
import { WorkspacePersonalizationPanel } from './WorkspacePersonalizationPanel'
import { WorkspaceRolePolicyPanel } from './WorkspaceRolePolicyPanel'
import { WorkspaceCompatibilityWarnings } from './WorkspaceCompatibilityWarnings'
import { useWorkspaceSettingsController } from './useWorkspaceSettingsController'

export function WorkspaceSettingsPage() {
  const controller = useWorkspaceSettingsController()
  const selectedRoleLabel = formatSettingsRoleLabel(controller.selectedRole)
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
      <SettingsPageHeader
        scope={controller.canManagePolicies ? 'Personal and organization' : 'Personal'}
        title="Navigation"
        description="Choose the navigation items, start page, and initial dashboard panels that shape the ThreatLens workspace."
        actions={
          <button
            type="button"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0 dark:border-cyan-900/40"
            disabled={
              controller.isRefreshing ||
              controller.personalMutationPending ||
              controller.roleMutationPending
            }
            title={controller.hasUnsavedChanges ? 'Discard unsaved navigation changes and load the latest revisions.' : undefined}
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
        }
      >
        {(controller.workspaceError || workspaceWarnings.length > 0) && (
          <div className="space-y-2 py-3">
            {controller.workspaceError && (
              <div role="alert" className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
                <p>{controller.workspaceError}</p>
                <p className="mt-1">Trusted local defaults remain active while navigation settings are unavailable.</p>
              </div>
            )}
            <WorkspaceCompatibilityWarnings warnings={workspaceWarnings} />
          </div>
        )}
      </SettingsPageHeader>

      <WorkspacePersonalizationPanel controller={controller} />
      {controller.canManagePolicies && <WorkspaceRolePolicyPanel controller={controller} />}

      <ConfirmDialog
        open={controller.discardReloadRequested}
        title="Discard navigation changes and reload?"
        description="All unsaved personal preferences and role defaults on this page will be discarded, then the latest server revisions will be loaded."
        confirmLabel="Discard and reload"
        confirmingLabel="Reloading..."
        confirmTone="danger"
        isConfirming={controller.isRefreshing}
        onCancel={() => controller.setDiscardReloadRequested(false)}
        onConfirm={() => void controller.discardAndReload()}
      />
      <ConfirmDialog
        open={controller.requestedRole !== null}
        title="Discard role default changes?"
        description={`Switching to ${controller.requestedRole ? formatSettingsRoleLabel(controller.requestedRole) : 'another role'} will discard the unsaved changes for ${selectedRoleLabel}.`}
        confirmLabel="Discard and switch"
        confirmingLabel="Switching..."
        confirmTone="danger"
        isConfirming={false}
        onCancel={controller.cancelRoleSelection}
        onConfirm={controller.confirmRoleSelection}
      />
      <ConfirmDialog
        open={controller.resetPersonalRequested}
        title="Reset personal navigation?"
        description="Your navigation visibility, order, start page, and initial dashboard panels will return to the organization defaults."
        confirmLabel="Reset navigation preferences"
        confirmingLabel="Resetting..."
        confirmTone="primary"
        isConfirming={controller.workspace.isResettingPreferences}
        onCancel={() => controller.setResetPersonalRequested(false)}
        onConfirm={() => void controller.resetPersonal()}
      />
      <ConfirmDialog
        open={controller.resetRoleRequested}
        title={`Reset ${selectedRoleLabel} navigation defaults?`}
        description="This changes the organization navigation defaults for every user with this built-in role. Personal choices are retained where the reset policy still permits them."
        confirmLabel="Reset navigation defaults"
        confirmingLabel="Resetting..."
        isConfirming={controller.resetRolePolicy.isPending}
        onCancel={() => controller.setResetRoleRequested(false)}
        onConfirm={() => controller.resetRolePolicy.mutate()}
      />
    </div>
  )
}
