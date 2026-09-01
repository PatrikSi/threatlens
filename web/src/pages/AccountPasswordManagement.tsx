import type { FormEvent } from 'react'

import { resolveApiErrorMessage } from '../api/errors'

export function PasswordManagementSection({
  ssoProvisioned,
  passwordLoginEnabled,
  providerName,
  currentPassword,
  newPassword,
  passwordFormError,
  mutationError,
  successNotice,
  isPending,
  onCurrentPasswordChange,
  onNewPasswordChange,
  onSubmit,
}: {
  ssoProvisioned: boolean
  passwordLoginEnabled: boolean
  providerName?: string | null
  currentPassword: string
  newPassword: string
  passwordFormError: string
  mutationError: unknown
  successNotice: string
  isPending: boolean
  onCurrentPasswordChange: (value: string) => void
  onNewPasswordChange: (value: string) => void
  onSubmit: (event: FormEvent) => void
}) {
  return (
    <section className="tl-surface rounded-xl p-3.5">
      <h2 className="font-display text-lg">Password</h2>
      {ssoProvisioned ? (
        <p className="mt-2 text-sm text-slate dark:text-slate-300">
          Password credentials are managed by{' '}
          {providerName || 'the identity provider'}.
        </p>
      ) : passwordLoginEnabled ? (
        <>
          <p
            id="password-change-impact"
            className="mt-2.5 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
          >
            Changing your password revokes every browser session and API token,
            including this browser session. You will need to sign in again.
          </p>
          <form
            className="mt-2.5 space-y-2.5"
            onSubmit={onSubmit}
            noValidate
            aria-describedby="password-change-impact"
          >
            <div>
              <label
                htmlFor="account-current-password"
                className="text-sm font-semibold"
              >
                Current password
              </label>
              <input
                id="account-current-password"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) =>
                  onCurrentPasswordChange(event.target.value)
                }
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                required
              />
            </div>
            <div>
              <label
                htmlFor="account-new-password"
                className="text-sm font-semibold"
              >
                New password
              </label>
              <input
                id="account-new-password"
                type="password"
                autoComplete="new-password"
                minLength={8}
                value={newPassword}
                onChange={(event) => onNewPasswordChange(event.target.value)}
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                required
              />
            </div>
            {passwordFormError && (
              <p
                role="alert"
                aria-live="assertive"
                aria-atomic="true"
                className="text-sm text-red-600 dark:text-red-300"
              >
                {passwordFormError}
              </p>
            )}
            {Boolean(mutationError) && !passwordFormError && (
              <p
                role="alert"
                aria-live="assertive"
                aria-atomic="true"
                className="text-sm text-red-600 dark:text-red-300"
              >
                {resolveApiErrorMessage(
                  mutationError,
                  'Password could not be changed',
                )}
              </p>
            )}
            {successNotice && (
              <p
                role="status"
                aria-live="polite"
                className="text-sm text-green-700 dark:text-green-300"
              >
                {successNotice}
              </p>
            )}
            <button
              className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]"
              disabled={isPending}
            >
              {isPending ? 'Updating password...' : 'Update password'}
            </button>
          </form>
        </>
      ) : (
        <p className="mt-2 text-sm text-slate dark:text-slate-300">
          Local password sign-in is not configured for this account.
        </p>
      )}
    </section>
  )
}
