import { useEffect, useState } from 'react'

import { ConfirmDialog } from '../components/ConfirmDialog'
import type { AdminUser, User, UserUpdateRequest } from '../types/api'
import { formatDateTime } from '../utils/datetime'
import { formatSettingsRoleLabel } from '../workspace/modulePresentation'
import {
  UserAuthenticationManagement,
  UserMfaChip,
  UserSettingsConflictNotice,
} from './UserRowSecurityControls'
import {
  buildPasswordResetConfirmation,
  buildUserSettingsConfirmation,
  resolveSelfLockoutWarnings,
  type UserSettingsDraft,
  type UserSettingsDraftConflict,
} from './userSettingsDraft'
import {
  formatAuthenticationMethods,
  hasAvailableSignInMethod,
  resolveAccountLabel,
  resolveCredentialManagementSource,
} from './userDirectoryModel'

type UserDirectoryRowProps = {
  user: AdminUser
  settingsDraft: UserSettingsDraft
  onSettingsDraftChange: (draft: UserSettingsDraft) => void
  passwordDraft: string
  onPasswordDraftChange: (value: string) => void
  actingUser: Pick<User, 'id' | 'role'> | null
  onSave: (payload: UserUpdateRequest) => void
  saving: boolean
  notice: {
    tone: 'success' | 'error'
    message: string
    action: 'settings' | 'password'
  } | null
  settingsConflict: UserSettingsDraftConflict | null
  onUseServerSettings: () => void
  onReapplySettings: () => void
  onResetMfa: () => void
}

export function UserDirectoryRow({
  user,
  settingsDraft,
  onSettingsDraftChange,
  passwordDraft,
  onPasswordDraftChange,
  actingUser,
  onSave,
  saving,
  notice,
  settingsConflict,
  onUseServerSettings,
  onReapplySettings,
  onResetMfa,
}: UserDirectoryRowProps) {
  const roleInputId = `user-role-${user.id}`
  const passwordInputId = `user-reset-password-${user.id}`
  const passwordManagedLocally =
    resolveCredentialManagementSource(user) === 'local'
  const roleManagedLocally = user.role_managed_by === 'local'
  const editableSettingsDraft = roleManagedLocally
    ? settingsDraft
    : { ...settingsDraft, role: user.role }
  const selfLockoutWarnings = resolveSelfLockoutWarnings(
    user,
    editableSettingsDraft,
    actingUser,
  )
  const settingsConfirmation = buildUserSettingsConfirmation(
    user,
    editableSettingsDraft,
    actingUser,
  )
  const passwordConfirmation = passwordManagedLocally
    ? buildPasswordResetConfirmation(user, passwordDraft)
    : null
  const [pendingConfirmationAction, setPendingConfirmationAction] = useState<
    'settings' | 'password' | null
  >(null)
  const [managementOpen, setManagementOpen] = useState(false)
  const pendingConfirmation = resolvePendingUserConfirmation(
    pendingConfirmationAction,
    settingsConfirmation,
    passwordConfirmation,
  )

  useEffect(() => {
    if (pendingConfirmationAction && !pendingConfirmation) {
      setPendingConfirmationAction(null)
    }
  }, [pendingConfirmation, pendingConfirmationAction])

  const confirmPendingChange = () => {
    if (!pendingConfirmation) return
    const payload = pendingConfirmation.payload
    setPendingConfirmationAction(null)
    onSave(payload)
  }

  return (
    <>
      <div className="rounded border border-slate/20 p-2.5 sm:p-3 dark:border-cyan-900/40">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="break-all font-semibold sm:break-normal">
              {user.email}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <span
                className={`tl-chip ${user.provisioning_source === 'oidc' ? 'tl-chip-info' : 'tl-chip-neutral'}`}
              >
                {resolveAccountLabel(user)}
              </span>
              <span className="tl-chip tl-chip-neutral">
                {formatSettingsRoleLabel(user.role)}
              </span>
              <span
                className={`tl-chip ${user.is_active ? 'tl-chip-success' : 'tl-chip-neutral'}`}
              >
                {user.is_active ? 'Active' : 'Disabled'}
              </span>
              {!user.is_approved && (
                <span className="tl-chip tl-chip-warning">
                  Pending approval
                </span>
              )}
              <UserMfaChip user={user} />
              {user.oidc_identity_status === 'linked_unavailable' && (
                <span className="tl-chip tl-chip-warning">SSO unavailable</span>
              )}
              {!hasAvailableSignInMethod(user) && (
                <span className="tl-chip tl-chip-warning">
                  No sign-in method
                </span>
              )}
            </div>
            <p className="mt-1.5 text-xs text-slate dark:text-slate-300">
              Created {formatDateTime(user.created_at)}
              {user.approved_at
                ? ` · Approved ${formatDateTime(user.approved_at)}`
                : ''}
            </p>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              Sign-in: {formatAuthenticationMethods(user)}
              {user.oidc_provider_name
                ? ` · Provider: ${user.oidc_provider_name}`
                : ''}
              {user.oidc_last_login_at
                ? ` · Last SSO sign-in ${formatDateTime(user.oidc_last_login_at)}`
                : ''}
              {` · ${user.active_session_count} tracked browser session${user.active_session_count === 1 ? '' : 's'}`}
            </p>
          </div>
          <button
            type="button"
            className="shrink-0 rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold dark:border-cyan-900/40"
            aria-expanded={managementOpen}
            aria-controls={`user-settings-${user.id} user-management-${user.id}`}
            aria-label={`${managementOpen ? 'Close management controls for' : 'Manage'} ${user.email}`}
            onClick={() => setManagementOpen((current) => !current)}
          >
            {managementOpen ? 'Close' : 'Manage'}
          </button>
        </div>

        <div
          id={`user-management-${user.id}`}
          className={`${managementOpen ? 'block' : 'hidden'} mt-3 border-t border-slate/15 pt-3 dark:border-cyan-900/30`}
        >
          <UserSettingsConflictNotice
            conflict={settingsConflict}
            onUseServer={onUseServerSettings}
            onReapply={onReapplySettings}
          />
          <div
            id={`user-settings-${user.id}`}
            className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(170px,1fr)_auto_auto_auto] xl:items-end"
          >
            <div>
              <p className="text-xs font-semibold text-slate dark:text-slate-300">
                Base role
              </p>
              {roleManagedLocally ? (
                <>
                  <label htmlFor={roleInputId} className="sr-only">
                    Base role for {user.email}
                  </label>
                  <select
                    id={roleInputId}
                    value={editableSettingsDraft.role}
                    onChange={(event) =>
                      onSettingsDraftChange({
                        ...editableSettingsDraft,
                        role: event.target.value as User['role'],
                      })
                    }
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  >
                    <option value="viewer">Viewer</option>
                    <option value="analyst">Analyst</option>
                    <option value="admin">Administrator</option>
                  </select>
                </>
              ) : (
                <div className="mt-1 text-sm">
                  <p className="font-semibold">
                    {formatSettingsRoleLabel(user.role)}
                  </p>
                  <p className="text-xs text-slate dark:text-slate-300">
                    Managed by {user.oidc_provider_name || 'SSO'}
                  </p>
                </div>
              )}
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                aria-label={`Account enabled for ${user.email}`}
                checked={editableSettingsDraft.isActive}
                onChange={(event) =>
                  onSettingsDraftChange({
                    ...editableSettingsDraft,
                    isActive: event.target.checked,
                  })
                }
              />
              Account enabled
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                aria-label={`Access approved for ${user.email}`}
                checked={editableSettingsDraft.isApproved}
                onChange={(event) =>
                  onSettingsDraftChange({
                    ...editableSettingsDraft,
                    isApproved: event.target.checked,
                  })
                }
              />
              Access approved
            </label>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40"
              disabled={
                saving || !settingsConfirmation || Boolean(settingsConflict)
              }
              onClick={() => setPendingConfirmationAction('settings')}
              aria-label={`Review account changes for ${user.email}`}
            >
              Review changes
            </button>
          </div>

          <div className="mt-3 border-t border-slate/15 pt-3 dark:border-cyan-900/30">
            <p className="text-xs font-semibold text-slate dark:text-slate-300">
              Sign-in methods
            </p>
            <UserAuthenticationManagement
              user={user}
              passwordInputId={passwordInputId}
              passwordDraft={passwordDraft}
              passwordConfirmationAvailable={Boolean(passwordConfirmation)}
              saving={saving}
              onPasswordDraftChange={onPasswordDraftChange}
              onReviewPasswordReset={() =>
                setPendingConfirmationAction('password')
              }
              onResetMfa={onResetMfa}
            />
          </div>

          {selfLockoutWarnings.length > 0 && (
            <div className="mt-3 rounded border border-amber-300/60 bg-amber-50/90 p-3 text-sm text-amber-900 dark:border-amber-800/40 dark:bg-amber-950/30 dark:text-amber-100">
              <p className="font-semibold">Self-access warning</p>
              <ul className="mt-2 list-disc space-y-1 pl-4">
                {selfLockoutWarnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
          {notice && (
            <p
              role={notice.tone === 'error' ? 'alert' : 'status'}
              aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
              aria-atomic="true"
              className={`mt-2 text-sm ${notice.tone === 'success' ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-600'}`}
            >
              {notice.message}
            </p>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(pendingConfirmation)}
        title={pendingConfirmation?.title ?? 'Review user change'}
        description={pendingConfirmation?.description}
        confirmLabel={pendingConfirmation?.confirmLabel ?? 'Confirm'}
        confirmTone={pendingConfirmation?.confirmTone ?? 'primary'}
        onCancel={() => setPendingConfirmationAction(null)}
        onConfirm={confirmPendingChange}
        confirmDisabled={!pendingConfirmation}
        isConfirming={saving}
      >
        {pendingConfirmation && (
          <div className="space-y-3">
            <div className="space-y-1">
              <p className="break-words font-semibold text-ink [overflow-wrap:anywhere] dark:text-white">
                {user.email}
              </p>
              <p className="text-xs text-slate dark:text-white/70">
                Base role: {formatSettingsRoleLabel(user.role)}
              </p>
            </div>
            {pendingConfirmation.warnings.length > 0 && (
              <div className="rounded-lg border border-red-300/60 bg-red-50/90 p-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/25 dark:text-red-100">
                <p className="font-semibold">Lockout risk</p>
                <ul className="mt-2 list-disc space-y-1 pl-4">
                  {pendingConfirmation.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
            <ul className="list-disc space-y-1 pl-4 text-sm text-slate-700 dark:text-white/80">
              {pendingConfirmation.details.map((detail) => (
                <li key={detail}>{detail}</li>
              ))}
            </ul>
          </div>
        )}
      </ConfirmDialog>
    </>
  )
}

function resolvePendingUserConfirmation(
  action: 'settings' | 'password' | null,
  settingsConfirmation: ReturnType<typeof buildUserSettingsConfirmation>,
  passwordConfirmation: ReturnType<typeof buildPasswordResetConfirmation>,
) {
  if (action === 'password') return passwordConfirmation
  if (action === 'settings') return settingsConfirmation
  return null
}
