import { FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { useAuth } from '../components/AuthContext'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { formatDateTime } from '../utils/datetime'
import { OIDCAccountStatus, OIDCStartResponse } from '../types/api'

export function AccountPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { markLoggedOut } = useAuth()
  const meQuery = useCurrentUser()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [passwordFormError, setPasswordFormError] = useState('')
  const [unlinkPassword, setUnlinkPassword] = useState('')
  const oidcStatusQuery = useQuery({
    queryKey: ['auth', 'oidc', 'account'],
    queryFn: () => apiFetch<OIDCAccountStatus>('/auth/oidc/account'),
  })
  const passwordLoginEnabled = meQuery.data?.password_login_enabled !== false
  const passwordDraftDirty =
    currentPassword.trim().length > 0 || newPassword.trim().length > 0 || unlinkPassword.trim().length > 0
  const confirmDiscardPasswordDraft = useUnsavedChangesWarning(
    passwordDraftDirty,
    'You have an unfinished password change. Leave without updating it?',
  )

  const changePassword = useMutation({
    mutationFn: () =>
      apiFetch('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      }),
    onMutate: () => {
      setPasswordFormError('')
    },
    onSuccess: () => {
      setCurrentPassword('')
      setNewPassword('')
      markLoggedOut()
      navigate('/login', {
        replace: true,
        state: { authMessage: 'Password updated. Sign in again with your new password.' },
      })
    },
  })
  const unlinkOidc = useMutation({
    mutationFn: () =>
      apiFetch('/auth/oidc/account', {
        method: 'DELETE',
        body: JSON.stringify({ current_password: unlinkPassword }),
      }),
    onSuccess: async () => {
      setUnlinkPassword('')
      await queryClient.invalidateQueries({ queryKey: ['auth', 'oidc', 'account'] })
    },
  })
  const linkOidc = useMutation({
    mutationFn: () => apiFetch<OIDCStartResponse>('/auth/oidc/link', { method: 'POST' }),
    onSuccess: ({ authorization_url: authorizationUrl }) => {
      window.location.assign(authorizationUrl)
    },
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const validationError = getPasswordChangeValidationError(currentPassword, newPassword)
    if (validationError) {
      setPasswordFormError(validationError)
      return
    }
    changePassword.mutate()
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_480px]">
      {confirmDiscardPasswordDraft.discardDialog}
      <section className="tl-surface rounded-xl p-4">
        <h2 className="font-display text-xl">Account</h2>
        {meQuery.data && (
          <div className="mt-3 space-y-1 text-sm">
            <p>
              <span className="font-semibold">Email:</span> {meQuery.data.email}
            </p>
            <p>
              <span className="font-semibold">Role:</span> {meQuery.data.role}
            </p>
            <p>
              <span className="font-semibold">Status:</span> {meQuery.data.is_active ? 'active' : 'inactive'}
            </p>
            <p>
              <span className="font-semibold">Created:</span> {formatDateTime(meQuery.data.created_at)}
            </p>
          </div>
        )}
      </section>

      <section className="tl-surface rounded-xl p-4">
        <h2 className="font-display text-xl">Single Sign-On</h2>
        {oidcStatusQuery.isLoading && <p className="mt-2 text-sm text-slate dark:text-slate-300">Loading identity status...</p>}
        {oidcStatusQuery.isError && (
          <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-300">
            Identity status could not be loaded.
          </p>
        )}
        {oidcStatusQuery.data && (
          <div className="mt-3 text-sm">
            {oidcStatusQuery.data.linked ? (
              <>
                <p>
                  Linked to <span className="font-semibold">{oidcStatusQuery.data.provider_name || 'OIDC'}</span>
                  {oidcStatusQuery.data.linked_email ? ` as ${oidcStatusQuery.data.linked_email}` : ''}.
                </p>
                {oidcStatusQuery.data.linked_at && (
                  <p className="mt-1 text-slate dark:text-slate-300">Linked {formatDateTime(oidcStatusQuery.data.linked_at)}</p>
                )}
                {passwordLoginEnabled ? (
                  <form
                    className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end"
                    onSubmit={(event) => {
                      event.preventDefault()
                      if (unlinkPassword.trim()) {
                        unlinkOidc.mutate()
                      }
                    }}
                  >
                    <div className="min-w-0 flex-1">
                      <label htmlFor="account-unlink-password" className="text-sm font-semibold">
                        Current password
                      </label>
                      <input
                        id="account-unlink-password"
                        type="password"
                        autoComplete="current-password"
                        value={unlinkPassword}
                        onChange={(event) => setUnlinkPassword(event.target.value)}
                        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                        required
                      />
                    </div>
                    <button
                      type="submit"
                      className="rounded border border-red-300/70 px-3 py-2 font-semibold text-red-700 dark:border-red-500/40 dark:text-red-200"
                      disabled={!unlinkPassword.trim() || unlinkOidc.isPending}
                    >
                      {unlinkOidc.isPending ? 'Unlinking...' : 'Unlink SSO'}
                    </button>
                  </form>
                ) : (
                  <p className="mt-3 text-slate dark:text-slate-300">
                    This is the only sign-in method for the account. An administrator must set a local password before it can be unlinked.
                  </p>
                )}
                {unlinkOidc.isError && (
                  <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-300">
                    {formatMutationError(unlinkOidc.error, 'Failed to unlink SSO.')}
                  </p>
                )}
              </>
            ) : oidcStatusQuery.data.available ? (
              <>
                <p className="text-slate dark:text-slate-300">
                  Link {oidcStatusQuery.data.provider_name || 'the configured identity provider'} to this account.
                </p>
                <button
                  type="button"
                  className="mt-3 rounded bg-ink px-3 py-2 font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
                  disabled={linkOidc.isPending}
                  onClick={() => linkOidc.mutate()}
                >
                  {linkOidc.isPending ? 'Connecting...' : 'Link SSO account'}
                </button>
                {linkOidc.isError && (
                  <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-300">
                    {formatMutationError(linkOidc.error, 'Failed to start SSO account linking.')}
                  </p>
                )}
              </>
            ) : (
              <p className="text-slate dark:text-slate-300">Single sign-on is not currently available.</p>
            )}
            {unlinkOidc.isSuccess && (
              <p role="status" aria-live="polite" className="mt-2 text-sm text-green-700 dark:text-green-400">
                SSO identity unlinked.
              </p>
            )}
          </div>
        )}
        {resolveOidcLinkNotice() && (
          <p role={resolveOidcLinkNotice()?.error ? 'alert' : 'status'} className="mt-3 text-sm text-slate dark:text-slate-200">
            {resolveOidcLinkNotice()?.message}
          </p>
        )}
      </section>

      <section className="tl-surface rounded-xl p-4 lg:col-start-2">
        <h2 className="font-display text-xl">Change Password</h2>
        {passwordLoginEnabled ? (
          <form className="mt-3 space-y-3" onSubmit={onSubmit} noValidate>
          <div>
            <label htmlFor="account-current-password" className="text-sm font-semibold">
              Current password
            </label>
            <input
              id="account-current-password"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => {
                setCurrentPassword(event.target.value)
                setPasswordFormError('')
              }}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              required
            />
          </div>
          <div>
            <label htmlFor="account-new-password" className="text-sm font-semibold">
              New password
            </label>
            <input
              id="account-new-password"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={newPassword}
              onChange={(event) => {
                setNewPassword(event.target.value)
                setPasswordFormError('')
              }}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              required
            />
          </div>
          {passwordFormError && (
            <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600 dark:text-red-300">
              {passwordFormError}
            </p>
          )}
          {changePassword.isError && !passwordFormError && (
            <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600 dark:text-red-300">
              {formatPasswordChangeError(changePassword.error)}
            </p>
          )}
          <button className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]" disabled={changePassword.isPending}>
            Update password
          </button>
          </form>
        ) : (
          <p className="mt-3 text-sm text-slate dark:text-slate-300">
            Local password sign-in is not configured for this account. An administrator can set a local password from Users.
          </p>
        )}
      </section>
    </div>
  )
}

function getPasswordChangeValidationError(currentPassword: string, newPassword: string) {
  if (!currentPassword.trim()) {
    return 'Enter your current password.'
  }
  if (!newPassword.trim()) {
    return 'Enter a new password.'
  }
  if (newPassword.length < 8) {
    return 'New password must be at least 8 characters.'
  }
  return ''
}

function formatPasswordChangeError(error: unknown) {
  if (error instanceof Error && error.message.trim()) {
    return `Failed to change password. ${error.message}`
  }
  return 'Failed to change password.'
}

function formatMutationError(error: unknown, fallback: string) {
  if (error instanceof Error && error.message.trim()) {
    return `${fallback} ${error.message}`
  }
  return fallback
}

function resolveOidcLinkNotice(): { message: string; error: boolean } | null {
  if (typeof window === 'undefined') {
    return null
  }
  const result = new URLSearchParams(window.location.search).get('oidc_link')
  if (!result) {
    return null
  }
  if (result === 'success') {
    return { message: 'SSO identity linked successfully.', error: false }
  }
  const messages: Record<string, string> = {
    identity_in_use: 'That SSO identity is already linked to another account.',
    account_already_linked: 'This account already has an SSO identity.',
    link_session_expired: 'The account-linking session expired. Start the link again.',
    invalid_state: 'The account-linking request expired or could not be verified.',
  }
  return { message: messages[result] ?? 'The SSO identity could not be linked.', error: true }
}
