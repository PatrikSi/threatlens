import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { User, UserCreateRequest, UserUpdateRequest } from '../types/api'
import { formatDateTime } from '../utils/datetime'

const ROLE_DEFINITIONS: Array<{ role: User['role']; summary: string; capabilities: string[] }> = [
  {
    role: 'admin',
    summary: 'Full administrative access across user management, global settings, and operational oversight.',
    capabilities: [
      'Manage users, approvals, and role changes',
      'Access audit logs and global administration surfaces',
      'Manage feeds, triage actions, tagging, and AI settings',
    ],
  },
  {
    role: 'analyst',
    summary: 'Operational user for daily feed management, investigation, and triage workflows.',
    capabilities: [
      'Manage feeds and perform triage actions',
      'Configure personal notifications and API tokens',
      'No access to user administration, audit logs, or global AI/tagging controls',
    ],
  },
  {
    role: 'viewer',
    summary: 'Read-oriented access for monitoring without operational or administrative mutation rights.',
    capabilities: [
      'View dashboard, feeds, and other read-only surfaces',
      'Access personal account settings, API tokens, and notifications',
      'Cannot change feeds, tags, or triage state',
    ],
  },
]

type UserSettingsDraft = {
  role: User['role']
  isActive: boolean
  isApproved: boolean
}

type UserConfirmationState = {
  title: string
  description: string
  confirmLabel: string
  confirmTone: 'danger' | 'primary'
  details: string[]
  payload: UserUpdateRequest
}

export function buildUserSettingsConfirmation(user: User, draft: UserSettingsDraft): UserConfirmationState | null {
  const payload: UserUpdateRequest = {}
  const details: string[] = []

  if (draft.role !== user.role) {
    payload.role = draft.role
    details.push(`Role will change from ${user.role} to ${draft.role}.`)
    if (draft.role === 'admin') {
      details.push('This grants full administrative access across user management, global settings, and operational controls.')
    } else if (user.role === 'admin') {
      details.push('This removes administrative access to user management, audit logs, and global settings.')
    }
  }

  if (draft.isActive !== user.is_active) {
    payload.is_active = draft.isActive
    details.push(
      draft.isActive ? 'Sign-in will be re-enabled for this account.' : 'Sign-in will be blocked until the account is reactivated.',
    )
  }

  if (draft.isApproved !== user.is_approved) {
    payload.is_approved = draft.isApproved
    details.push(
      draft.isApproved ? 'The account will move out of pending approval.' : 'The account will return to pending approval.',
    )
  }

  if (!details.length) {
    return null
  }

  return {
    title: 'Apply privileged user changes?',
    description: 'Review the account changes below before they are applied.',
    confirmLabel: 'Apply user changes',
    confirmTone: 'primary',
    details,
    payload,
  }
}

export function buildPasswordResetConfirmation(user: User, nextPassword: string): UserConfirmationState | null {
  const trimmedPassword = nextPassword.trim()
  if (trimmedPassword.length < 8) {
    return null
  }

  return {
    title: 'Reset user password?',
    description: `This immediately replaces the current password for ${user.email}.`,
    confirmLabel: 'Reset password',
    confirmTone: 'primary',
    details: [
      `You are updating credentials for ${user.email}.`,
      'The current password will stop working as soon as you confirm.',
      `The new password meets the minimum length requirement with ${trimmedPassword.length} characters.`,
      'Share the new password through a secure channel.',
    ],
    payload: { password: trimmedPassword },
  }
}

export function UsersPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [rowNoticeByUserId, setRowNoticeByUserId] = useState<
    Record<string, { tone: 'success' | 'error'; message: string; action: 'settings' | 'password' }>
  >({})
  const [createForm, setCreateForm] = useState<UserCreateRequest>({
    email: '',
    password: '',
    role: 'viewer',
    is_active: true,
    is_approved: true,
  })

  const usersQuery = useQuery({
    queryKey: ['users'],
    queryFn: () => apiFetch<User[]>('/users'),
  })

  const createUser = useMutation({
    mutationFn: (payload: UserCreateRequest) =>
      apiFetch<User>('/users', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setCreateForm({ email: '', password: '', role: 'viewer', is_active: true, is_approved: true })
      void queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })

  const updateUser = useMutation({
    mutationFn: (payload: { id: string; body: UserUpdateRequest }) =>
      apiFetch<User>(`/users/${payload.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload.body),
      }),
    onMutate: (payload) => {
      setRowNoticeByUserId((current) => {
        const next = { ...current }
        delete next[payload.id]
        return next
      })
    },
    onSuccess: (_user, payload) => {
      setRowNoticeByUserId((current) => ({
        ...current,
        [payload.id]: {
          tone: 'success',
          message: payload.body.password ? 'Password updated.' : 'User settings updated.',
          action: payload.body.password ? 'password' : 'settings',
        },
      }))
      void queryClient.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (error, payload) => {
      setRowNoticeByUserId((current) => ({
        ...current,
        [payload.id]: {
          tone: 'error',
          message: resolveUsersMutationError(error),
          action: payload.body.password ? 'password' : 'settings',
        },
      }))
    },
  })

  const filteredUsers = useMemo(() => {
    const users = usersQuery.data ?? []
    const normalized = search.trim().toLowerCase()
    if (!normalized) return users
    return users.filter(
      (user) =>
        user.email.toLowerCase().includes(normalized) ||
        user.role.includes(normalized) ||
        (user.is_approved ? 'approved' : 'pending').includes(normalized),
    )
  }, [usersQuery.data, search])

  const onCreateSubmit = (event: FormEvent) => {
    event.preventDefault()
    createUser.mutate(createForm)
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Create User</h2>
        <form className="mt-3 space-y-3" onSubmit={onCreateSubmit}>
          <div>
            <label className="text-sm font-semibold">Email</label>
            <input
              value={createForm.email}
              onChange={(event) => setCreateForm((f) => ({ ...f, email: event.target.value }))}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              type="email"
              required
            />
          </div>
          <div>
            <label className="text-sm font-semibold">Password</label>
            <input
              value={createForm.password}
              onChange={(event) => setCreateForm((f) => ({ ...f, password: event.target.value }))}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              type="password"
              minLength={8}
              required
            />
          </div>
          <div>
            <label className="text-sm font-semibold">Role</label>
            <select
              value={createForm.role}
              onChange={(event) => setCreateForm((f) => ({ ...f, role: event.target.value as User['role'] }))}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="viewer">viewer</option>
              <option value="analyst">analyst</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={createForm.is_active}
              onChange={(event) => setCreateForm((f) => ({ ...f, is_active: event.target.checked }))}
            />
            Active
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={createForm.is_approved}
              onChange={(event) => setCreateForm((f) => ({ ...f, is_approved: event.target.checked }))}
            />
            Approved
          </label>
          {createUser.isError && <p className="text-sm text-red-600">Failed to create user.</p>}
          <button className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]" disabled={createUser.isPending}>
            Create User
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-xl">User Directory</h2>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search users..."
            className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
          />
        </div>

        <details className="mt-3 rounded-lg border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.04]">
          <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900 dark:text-white">
            <span className="inline-flex items-center gap-2">
              <span>Role Definitions</span>
              <span className="text-xs font-normal text-slate dark:text-slate-300">Expand for admin, analyst, and viewer access boundaries</span>
            </span>
          </summary>
          <div className="mt-3 grid gap-3 lg:grid-cols-3">
            {ROLE_DEFINITIONS.map((entry) => (
              <section key={entry.role} className="rounded-lg border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-900 dark:text-white">{entry.role}</h3>
                <p className="mt-1 text-sm text-slate dark:text-slate-300">{entry.summary}</p>
                <ul className="mt-3 list-disc space-y-1 pl-4 text-sm text-slate-900 dark:text-slate-200">
                  {entry.capabilities.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </details>

        <div className="mt-3 space-y-2">
          {filteredUsers.map((user) => (
            <UserRow
              key={user.id}
              user={user}
              onSave={(body) => updateUser.mutate({ id: user.id, body })}
              saving={updateUser.isPending && updateUser.variables?.id === user.id}
              notice={rowNoticeByUserId[user.id] ?? null}
            />
          ))}

          {usersQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading users...</p>}
          {usersQuery.isError && <p className="text-sm text-red-600">{resolveUsersError(usersQuery.error)}</p>}
        </div>
      </section>
    </div>
  )
}

function UserRow({
  user,
  onSave,
  saving,
  notice,
}: {
  user: User
  onSave: (payload: UserUpdateRequest) => void
  saving: boolean
  notice: { tone: 'success' | 'error'; message: string; action: 'settings' | 'password' } | null
}) {
  const [role, setRole] = useState<User['role']>(user.role)
  const [isActive, setIsActive] = useState(user.is_active)
  const [isApproved, setIsApproved] = useState(user.is_approved)
  const [resetPassword, setResetPassword] = useState('')
  const settingsConfirmation = buildUserSettingsConfirmation(user, { role, isActive, isApproved })
  const passwordConfirmation = buildPasswordResetConfirmation(user, resetPassword)
  const [pendingConfirmationAction, setPendingConfirmationAction] = useState<'settings' | 'password' | null>(null)
  const pendingConfirmation =
    pendingConfirmationAction === 'password'
      ? passwordConfirmation
      : pendingConfirmationAction === 'settings'
        ? settingsConfirmation
        : null

  useEffect(() => {
    setRole(user.role)
    setIsActive(user.is_active)
    setIsApproved(user.is_approved)
  }, [user.is_active, user.is_approved, user.role])

  useEffect(() => {
    if (notice?.tone === 'success' && notice.action === 'password') {
      setResetPassword('')
    }
  }, [notice])

  useEffect(() => {
    if (pendingConfirmationAction && !pendingConfirmation) {
      setPendingConfirmationAction(null)
    }
  }, [pendingConfirmation, pendingConfirmationAction])

  const confirmPendingChange = () => {
    if (!pendingConfirmation) {
      return
    }

    const payload = pendingConfirmation.payload
    setPendingConfirmationAction(null)
    onSave(payload)
  }

  return (
    <>
      <div className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-semibold">{user.email}</p>
            <p className="text-xs text-slate dark:text-slate-300">
              Created {formatDateTime(user.created_at)}
              {user.approved_at ? ` · Approved ${formatDateTime(user.approved_at)}` : ''}
            </p>
            {!user.is_approved && (
              <p className="mt-1 inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                Pending approval
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={role}
              onChange={(event) => setRole(event.target.value as User['role'])}
              className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="viewer">viewer</option>
              <option value="analyst">analyst</option>
              <option value="admin">admin</option>
            </select>
            <label className="flex items-center gap-1 text-sm">
              <input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} />
              Active
            </label>
            <label className="flex items-center gap-1 text-sm">
              <input type="checkbox" checked={isApproved} onChange={(event) => setIsApproved(event.target.checked)} />
              Approved
            </label>
            <button
              className="rounded border border-slate/30 px-3 py-1 text-sm font-semibold dark:border-cyan-900/40"
              disabled={saving || !settingsConfirmation}
              onClick={() => setPendingConfirmationAction('settings')}
            >
              Review user changes
            </button>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="password"
            placeholder="New password (min 8 chars)"
            value={resetPassword}
            onChange={(event) => setResetPassword(event.target.value)}
            className="w-full rounded border border-slate/30 bg-white px-2 py-1 text-sm sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
          />
          <button
            className="rounded border border-slate/30 px-3 py-1 text-sm dark:border-cyan-900/40"
            disabled={saving || !passwordConfirmation}
            onClick={() => setPendingConfirmationAction('password')}
          >
            Review password reset
          </button>
        </div>
        {notice && (
          <p className={`mt-2 text-sm ${notice.tone === 'success' ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-600'}`}>
            {notice.message}
          </p>
        )}
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
              <p className="font-semibold text-ink dark:text-white">{user.email}</p>
              <p className="text-xs text-slate dark:text-white/70">Role: {user.role}</p>
            </div>
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

function resolveUsersError(error: unknown): string {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return `Failed to load users: ${error.message}`
  }
  return 'Failed to load users.'
}

function resolveUsersMutationError(error: unknown): string {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return error.message
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return 'Failed to update user.'
}
