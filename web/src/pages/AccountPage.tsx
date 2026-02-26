import { FormEvent, useState } from 'react'
import { useMutation } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'

export function AccountPage() {
  const meQuery = useCurrentUser()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')

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
    },
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    changePassword.mutate()
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_480px]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
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
              <span className="font-semibold">Created:</span> {new Date(meQuery.data.created_at).toLocaleString()}
            </p>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
        <h2 className="font-display text-xl">Change Password</h2>
        <form className="mt-3 space-y-3" onSubmit={onSubmit}>
          <div>
            <label className="text-sm font-semibold">Current password</label>
            <input
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
              required
            />
          </div>
          <div>
            <label className="text-sm font-semibold">New password</label>
            <input
              type="password"
              minLength={8}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
              required
            />
          </div>
          {changePassword.isError && <p className="text-sm text-red-600">Failed to change password.</p>}
          {changePassword.isSuccess && <p className="text-sm text-green-600">Password updated.</p>}
          <button className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-ink" disabled={changePassword.isPending}>
            Update password
          </button>
        </form>
      </section>
    </div>
  )
}
