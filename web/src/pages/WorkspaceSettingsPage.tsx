import { RefreshCw, ShieldCheck, UserRound, type LucideIcon } from 'lucide-react'
import { useRef, useState, type KeyboardEvent, type RefObject } from 'react'

import { ConfirmDialog } from '../components/ConfirmDialog'
import { SettingsPageHeader } from '../components/SettingsPageHeader'
import { formatSettingsRoleLabel } from '../workspace/modulePresentation'
import { WorkspacePersonalizationPanel } from './WorkspacePersonalizationPanel'
import { WorkspaceRolePolicyPanel } from './WorkspaceRolePolicyPanel'
import { WorkspaceCompatibilityWarnings } from './WorkspaceCompatibilityWarnings'
import {
  useWorkspaceSettingsController,
  type WorkspaceSettingsController,
} from './useWorkspaceSettingsController'

type NavigationSettingsView = 'personal' | 'role-defaults'

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
    <div className="space-y-3">
      <SettingsPageHeader
        scope={controller.canReadPolicies ? 'Personal and organization' : 'Personal'}
        title="Navigation"
        description="Configure the top navigation, Settings sidebar, start page, and initial dashboard panels that shape the ThreatLens workspace."
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

      {controller.canReadPolicies ? (
        <AdminNavigationSettings controller={controller} />
      ) : (
        <WorkspacePersonalizationPanel controller={controller} />
      )}

      <ConfirmDialog
        open={controller.discardReloadRequested}
        title="Discard navigation changes and reload?"
        description={controller.canManagePolicies
          ? 'All unsaved personal preferences and role defaults on this page will be discarded, then the latest server revisions will be loaded.'
          : 'All unsaved personal navigation preferences will be discarded, then the latest server revisions will be loaded.'}
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
        open={controller.canManagePolicies && controller.resetRoleRequested}
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

function AdminNavigationSettings({ controller }: { controller: WorkspaceSettingsController }) {
  const [activeView, setActiveView] = useState<NavigationSettingsView>('personal')
  const personalTabRef = useRef<HTMLButtonElement>(null)
  const roleDefaultsTabRef = useRef<HTMLButtonElement>(null)
  const roleNeedsAttention = Boolean(
    controller.roleError ||
    controller.roleValidation ||
    controller.selectedPolicyWarnings.length > 0
  )
  const views: readonly NavigationSettingsView[] = ['personal', 'role-defaults']

  function selectAdjacentView(
    event: KeyboardEvent<HTMLButtonElement>,
    currentView: NavigationSettingsView,
  ) {
    const currentIndex = views.indexOf(currentView)
    let nextIndex: number | null = null

    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % views.length
    if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + views.length) % views.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = views.length - 1
    if (nextIndex === null) return

    event.preventDefault()
    const nextView = views[nextIndex]
    setActiveView(nextView)
    if (nextView === 'personal') personalTabRef.current?.focus()
    if (nextView === 'role-defaults') roleDefaultsTabRef.current?.focus()
  }

  return (
    <div className="space-y-3">
      <div className="tl-surface rounded-xl p-1.5">
        <div
          role="tablist"
          aria-label="Navigation settings scope"
          aria-orientation="horizontal"
          className="grid grid-cols-2 gap-1"
        >
          <NavigationSettingsTab
            id="personal-navigation-tab"
            controls="personal-navigation-panel"
            icon={UserRound}
            label="Personal"
            selected={activeView === 'personal'}
            unsaved={controller.personalDirty}
            needsAttention={Boolean(controller.personalError)}
            tabRef={personalTabRef}
            onClick={() => setActiveView('personal')}
            onKeyDown={(event) => selectAdjacentView(event, 'personal')}
          />
          <NavigationSettingsTab
            id="role-defaults-navigation-tab"
            controls="role-defaults-navigation-panel"
            icon={ShieldCheck}
            label="Role defaults"
            selected={activeView === 'role-defaults'}
            unsaved={controller.roleDirty}
            needsAttention={roleNeedsAttention}
            tabRef={roleDefaultsTabRef}
            onClick={() => setActiveView('role-defaults')}
            onKeyDown={(event) => selectAdjacentView(event, 'role-defaults')}
          />
        </div>
        <p className="px-2 pb-1 pt-1.5 text-xs text-slate dark:text-slate-400">
          {activeView === 'personal'
            ? 'Customize your top navigation, Settings sidebar, start page, and initial dashboard.'
            : controller.canManagePolicies
              ? 'Set organization top-navigation and Settings-sidebar defaults for each built-in role.'
              : 'Review organization top-navigation and Settings-sidebar defaults for each built-in role.'}
        </p>
      </div>

      <div
        id="personal-navigation-panel"
        role="tabpanel"
        aria-labelledby="personal-navigation-tab"
        tabIndex={0}
        hidden={activeView !== 'personal'}
      >
        <WorkspacePersonalizationPanel controller={controller} />
      </div>
      <div
        id="role-defaults-navigation-panel"
        role="tabpanel"
        aria-labelledby="role-defaults-navigation-tab"
        tabIndex={0}
        hidden={activeView !== 'role-defaults'}
      >
        <WorkspaceRolePolicyPanel controller={controller} />
      </div>
    </div>
  )
}

function NavigationSettingsTab({
  id,
  controls,
  icon: Icon,
  label,
  selected,
  unsaved,
  needsAttention,
  tabRef,
  onClick,
  onKeyDown,
}: {
  id: string
  controls: string
  icon: LucideIcon
  label: string
  selected: boolean
  unsaved: boolean
  needsAttention: boolean
  tabRef: RefObject<HTMLButtonElement | null>
  onClick: () => void
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void
}) {
  return (
    <button
      ref={tabRef}
      id={id}
      type="button"
      role="tab"
      aria-controls={controls}
      aria-selected={selected}
      tabIndex={selected ? 0 : -1}
      className={`flex min-h-11 min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-lg px-2.5 py-2 text-left text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan sm:min-h-0 sm:px-3 ${
        selected
          ? 'bg-ink text-white shadow-sm dark:bg-cyan dark:text-[#053c2e]'
          : 'text-slate hover:bg-slate/5 dark:text-slate-200 dark:hover:bg-white/[0.04]'
      }`}
      onClick={onClick}
      onKeyDown={onKeyDown}
    >
      <span className="inline-flex min-w-0 items-center gap-2">
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{label}</span>
      </span>
      {(unsaved || needsAttention) && (
        <span className="ml-auto inline-flex flex-wrap justify-end gap-1">
          {unsaved && (
            <span className={`rounded-full px-1.5 py-0.5 text-[11px] font-semibold leading-none ${
              selected
                ? 'bg-white/15 text-current dark:bg-[#053c2e]/15'
                : 'bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-100'
            }`}>
              Unsaved
            </span>
          )}
          {needsAttention && (
            <span className={`rounded-full px-1.5 py-0.5 text-[11px] font-semibold leading-none ${
              selected
                ? 'bg-white/15 text-current dark:bg-[#053c2e]/15'
                : 'bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-100'
            }`}>
              Needs attention
            </span>
          )}
        </span>
      )}
    </button>
  )
}
