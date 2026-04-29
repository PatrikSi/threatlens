import { FormEvent, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { useAuth } from '../components/AuthContext'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { formatDateTime } from '../utils/datetime'

export function AccountPage() {
  const navigate = useNavigate()
  const { markLoggedOut } = useAuth()
  const meQuery = useCurrentUser()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const passwordDraftDirty = currentPassword.trim().length > 0 || newPassword.trim().length > 0
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

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
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
        <h2 className="font-display text-xl">Change Password</h2>
        <form className="mt-3 space-y-3" onSubmit={onSubmit}>
          <div>
            <label htmlFor="account-current-password" className="text-sm font-semibold">
              Current password
            </label>
            <input
              id="account-current-password"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
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
              onChange={(event) => setNewPassword(event.target.value)}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              required
            />
          </div>
          {changePassword.isError && (
            <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
              Failed to change password.
            </p>
          )}
          <button className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]" disabled={changePassword.isPending}>
            Update password
          </button>
        </form>
      </section>
    </div>
  )
}
