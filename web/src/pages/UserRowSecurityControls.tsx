import type { AdminUser } from '../types/api'
import { resolveCredentialManagementSource } from './userDirectoryModel'
import type { UserSettingsDraftConflict } from './userSettingsDraft'

export function UserMfaChip({ user }: { user: AdminUser }) {
  if (
    !user.password_login_enabled ||
    resolveCredentialManagementSource(user) !== 'local'
  ) {
    return (
      <span className="tl-chip tl-chip-info">Local MFA not applicable</span>
    )
  }
  return user.mfa_enabled ? (
    <span className="tl-chip tl-chip-success">Local MFA enabled</span>
  ) : (
    <span className="tl-chip tl-chip-warning">Local MFA not enabled</span>
  )
}

export function UserSettingsConflictNotice({
  conflict,
  onUseServer,
  onReapply,
}: {
  conflict: UserSettingsDraftConflict | null
  onUseServer: () => void
  onReapply: () => void
}) {
  if (!conflict) return null
  return (
    <div
      role="alert"
      className="mb-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
    >
      <p className="font-semibold">This account changed on the server</p>
      <p className="mt-1">
        Review the overlapping fields before this row can be saved.
      </p>
      <ul className="mt-2 list-disc space-y-1 pl-4">
        {conflict.overlappingFields.map((field) => (
          <li key={field.field}>
            {field.label}: server has {field.serverValue}; your draft has{' '}
            {field.operatorValue}
          </li>
        ))}
      </ul>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          className="min-h-11 rounded border border-current px-3 py-2 font-semibold"
          onClick={onUseServer}
        >
          Use server values
        </button>
        <button
          type="button"
          className="min-h-11 rounded bg-ink px-3 py-2 font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
          onClick={onReapply}
        >
          Reapply my changes
        </button>
      </div>
    </div>
  )
}

export function UserAuthenticationManagement({
  user,
  passwordInputId,
  passwordDraft,
  passwordConfirmationAvailable,
  saving,
  onPasswordDraftChange,
  onReviewPasswordReset,
  onResetMfa,
}: {
  user: AdminUser
  passwordInputId: string
  passwordDraft: string
  passwordConfirmationAvailable: boolean
  saving: boolean
  onPasswordDraftChange: (value: string) => void
  onReviewPasswordReset: () => void
  onResetMfa: () => void
}) {
  if (resolveCredentialManagementSource(user) !== 'local') {
    return (
      <p className="mt-1 text-sm text-slate dark:text-slate-300">
        Credentials are managed by{' '}
        {user.oidc_provider_name || 'the identity provider'}.
      </p>
    )
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <label htmlFor={passwordInputId} className="sr-only">
        New password for {user.email}
      </label>
      <input
        id={passwordInputId}
        type="password"
        autoComplete="new-password"
        minLength={8}
        maxLength={256}
        placeholder="New password (at least 8 characters)"
        value={passwordDraft}
        onChange={(event) => onPasswordDraftChange(event.target.value)}
        className="w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
      />
      <button
        type="button"
        className="rounded border border-slate/30 px-3 py-1.5 text-sm dark:border-cyan-900/40"
        disabled={saving || !passwordConfirmationAvailable}
        onClick={onReviewPasswordReset}
        aria-label={`Review password reset for ${user.email}`}
      >
        Review password reset
      </button>
      {user.mfa_enabled && (
        <button
          type="button"
          className="tl-button-danger rounded px-3 py-1.5 text-sm font-semibold"
          disabled={saving}
          onClick={onResetMfa}
          aria-label={`Reset multi-factor authentication for ${user.email}`}
        >
          Reset MFA
        </button>
      )}
    </div>
  )
}
