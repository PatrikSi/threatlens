import { FormEvent, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { User, UserCreateRequest, UserUpdateRequest } from '../types/api'

export function UsersPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
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
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
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

        <div className="mt-3 space-y-2">
          {filteredUsers.map((user) => (
            <UserRow key={user.id} user={user} onSave={(body) => updateUser.mutate({ id: user.id, body })} saving={updateUser.isPending} />
          ))}

          {usersQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading users...</p>}
          {usersQuery.isError && <p className="text-sm text-red-600">{resolveUsersError(usersQuery.error)}</p>}
        </div>
      </section>
    </div>
  )
}

function UserRow({ user, onSave, saving }: { user: User; onSave: (payload: UserUpdateRequest) => void; saving: boolean }) {
  const [role, setRole] = useState<User['role']>(user.role)
  const [isActive, setIsActive] = useState(user.is_active)
  const [isApproved, setIsApproved] = useState(user.is_approved)
  const [resetPassword, setResetPassword] = useState('')
  const trimmedPassword = resetPassword.trim()

  return (
    <div className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-semibold">{user.email}</p>
          <p className="text-xs text-slate dark:text-slate-300">
            Created {new Date(user.created_at).toLocaleString()}
            {user.approved_at ? ` · Approved ${new Date(user.approved_at).toLocaleString()}` : ''}
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
            disabled={saving}
            onClick={() => onSave({ role, is_active: isActive, is_approved: isApproved })}
          >
            Save user settings
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
          disabled={saving || trimmedPassword.length < 8}
          onClick={() => {
            onSave({ password: trimmedPassword })
            setResetPassword('')
          }}
        >
          Save password
        </button>
      </div>
    </div>
  )
}

function resolveUsersError(error: unknown): string {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return `Failed to load users: ${error.message}`
  }
  return 'Failed to load users.'
}
