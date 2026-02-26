import { FormEvent, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { User, UserCreateRequest, UserUpdateRequest } from '../types/api'

export function UsersPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [createForm, setCreateForm] = useState<UserCreateRequest>({
    email: '',
    password: '',
    role: 'viewer',
    is_active: true,
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
      setCreateForm({ email: '', password: '', role: 'viewer', is_active: true })
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
    return users.filter((user) => user.email.toLowerCase().includes(normalized) || user.role.includes(normalized))
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
          {createUser.isError && <p className="text-sm text-red-600">Failed to create user.</p>}
          <button className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-ink" disabled={createUser.isPending}>
            Create User
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-display text-xl">User Directory</h2>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search users..."
            className="w-64 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          />
        </div>

        <div className="mt-3 space-y-2">
          {filteredUsers.map((user) => (
            <UserRow key={user.id} user={user} onSave={(body) => updateUser.mutate({ id: user.id, body })} saving={updateUser.isPending} />
          ))}

          {usersQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading users...</p>}
          {usersQuery.isError && <p className="text-sm text-red-600">Failed to load users.</p>}
        </div>
      </section>
    </div>
  )
}

function UserRow({ user, onSave, saving }: { user: User; onSave: (payload: UserUpdateRequest) => void; saving: boolean }) {
  const [role, setRole] = useState<User['role']>(user.role)
  const [isActive, setIsActive] = useState(user.is_active)
  const [resetPassword, setResetPassword] = useState('')

  return (
    <div className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-semibold">{user.email}</p>
          <p className="text-xs text-slate dark:text-slate-300">Created {new Date(user.created_at).toLocaleString()}</p>
        </div>
        <div className="flex items-center gap-2">
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
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          type="password"
          placeholder="New password (optional)"
          value={resetPassword}
          onChange={(event) => setResetPassword(event.target.value)}
          className="w-64 rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        />
        <button
          className="rounded border border-slate/30 px-3 py-1 text-sm dark:border-cyan-900/40"
          disabled={saving}
          onClick={() => {
            const payload: UserUpdateRequest = { role, is_active: isActive }
            if (resetPassword.trim()) payload.password = resetPassword.trim()
            onSave(payload)
            setResetPassword('')
          }}
        >
          Save
        </button>
      </div>
    </div>
  )
}
