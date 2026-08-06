import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { AdminUser, User, UserCreateRequest, UserUpdateRequest } from '../types/api'
import { formatDateTime } from '../utils/datetime'
import {
  buildCreateUserConfirmation,
  buildPasswordResetConfirmation,
  buildUserSettingsConfirmation,
  createUserSettingsDraft,
  CreateUserConfirmationState,
  resolveSelfLockoutWarnings,
  syncUserSettingsDrafts,
  UserSettingsDraft,
} from './userSettingsDraft'

const ROLE_DEFINITIONS: Array<{
  role: User['role']
  summary: string
  capabilities: string[]
}> = [
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

const DEFAULT_CREATE_USER_FORM: UserCreateRequest = {
  email: '',
  password: '',
  role: 'viewer',
  is_active: true,
  is_approved: true,
}

export function UsersPage() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [search, setSearch] = useState('')
  const [accountFilter, setAccountFilter] = useState<'all' | 'local' | 'oidc' | 'hybrid'>('all')
  const [rowNoticeByUserId, setRowNoticeByUserId] = useState<
    Record<
      string,
      {
        tone: 'success' | 'error'
        message: string
        action: 'settings' | 'password'
      }
    >
  >({})
  const [settingsDraftsByUserId, setSettingsDraftsByUserId] = useState<Record<string, UserSettingsDraft>>({})
  const [passwordDraftsByUserId, setPasswordDraftsByUserId] = useState<Record<string, string>>({})
  const settingsDraftBaselinesByUserIdRef = useRef<Record<string, UserSettingsDraft>>({})
  const [createForm, setCreateForm] = useState<UserCreateRequest>(DEFAULT_CREATE_USER_FORM)
  const [pendingCreateConfirmation, setPendingCreateConfirmation] = useState<CreateUserConfirmationState | null>(null)
  const [createUserOpen, setCreateUserOpen] = useState(false)

  const usersQuery = useQuery({
    queryKey: ['users'],
    queryFn: () => apiFetch<AdminUser[]>('/users'),
  })

  const createUser = useMutation({
    mutationFn: (payload: UserCreateRequest) =>
      apiFetch<AdminUser>('/users', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setCreateForm(DEFAULT_CREATE_USER_FORM)
      setCreateUserOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })

  const updateUser = useMutation({
    mutationFn: (payload: { id: string; body: UserUpdateRequest }) =>
      apiFetch<AdminUser>(`/users/${payload.id}`, {
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
      if (payload.body.password) {
        setPasswordDraftsByUserId((current) => ({
          ...current,
          [payload.id]: '',
        }))
      }
      setRowNoticeByUserId((current) => ({
        ...current,
        [payload.id]: {
          tone: 'success',
          message: payload.body.password ? 'Password updated.' : 'User settings updated.',
          action: payload.body.password ? 'password' : 'settings',
        },
      }))
      void queryClient.invalidateQueries({ queryKey: ['users'] })
      if (currentUserQuery.data?.id === payload.id) {
        void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
      }
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
    return users.filter((user) => {
      if (accountFilter !== 'all' && resolveAccountCategory(user) !== accountFilter) {
        return false
      }
      if (!normalized) {
        return true
      }
      return (
        user.email.toLowerCase().includes(normalized) ||
        user.role.includes(normalized) ||
        (user.is_approved ? 'approved' : 'pending').includes(normalized) ||
        resolveAccountLabel(user).toLowerCase().includes(normalized) ||
        (user.oidc_provider_name || '').toLowerCase().includes(normalized)
      )
    })
  }, [accountFilter, usersQuery.data, search])

  useEffect(() => {
    const users = usersQuery.data ?? []
    setSettingsDraftsByUserId((current) => {
      const synced = syncUserSettingsDrafts(users, current, settingsDraftBaselinesByUserIdRef.current)
      for (const user of users) {
        if (user.role_managed_by === 'oidc') {
          synced.drafts[user.id].role = user.role
        }
      }
      settingsDraftBaselinesByUserIdRef.current = synced.baselines
      return synced.drafts
    })
    setPasswordDraftsByUserId((current) => {
      const validUserIds = new Set(users.filter((user) => user.password_managed_by === 'local').map((user) => user.id))
      const next = Object.fromEntries(
        Object.entries(current).filter(([userId, draft]) => validUserIds.has(userId) && draft.trim()),
      ) as Record<string, string>
      return next
    })
  }, [usersQuery.data])

  const hasUnsavedUserSettingsChanges = useMemo(
    () => hasDirtyUserSettingsDrafts(settingsDraftsByUserId, settingsDraftBaselinesByUserIdRef.current),
    [settingsDraftsByUserId],
  )
  const hasUnsavedPasswordDrafts = useMemo(
    () => Object.values(passwordDraftsByUserId).some((value) => value.trim().length > 0),
    [passwordDraftsByUserId],
  )
  const hasUnsavedCreateUserChanges = isCreateUserFormDirty(createForm)
  const confirmDiscardUnsavedUserSettingsChanges = useUnsavedChangesWarning(
    hasUnsavedUserSettingsChanges || hasUnsavedPasswordDrafts || hasUnsavedCreateUserChanges,
    'Discard unsaved user changes?',
  )

  const onCreateSubmit = (event: FormEvent) => {
    event.preventDefault()
    const confirmation = buildCreateUserConfirmation(createForm)
    if (!confirmation) {
      return
    }
    setPendingCreateConfirmation(confirmation)
  }

  const confirmCreateUser = () => {
    if (!pendingCreateConfirmation) {
      return
    }

    createUser.mutate(pendingCreateConfirmation.payload)
    setPendingCreateConfirmation(null)
  }

  const createUserFormVisible = createUserOpen || (usersQuery.data?.length ?? 0) === 0

  return (
    <>
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-xl">User Directory</h2>
            <p className="text-xs text-slate dark:text-slate-300">
              {filteredUsers.length} of {usersQuery.data?.length ?? 0} accounts
            </p>
          </div>
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
            {(usersQuery.data?.length ?? 0) > 0 && (
              <button
                type="button"
                className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
                aria-expanded={createUserFormVisible}
                aria-controls="create-user-form"
                onClick={() => setCreateUserOpen((current) => !current)}
              >
                {createUserFormVisible ? 'Close form' : 'New local user'}
              </button>
            )}
            <label htmlFor="user-account-filter" className="sr-only">
              Filter by account type
            </label>
            <select
              id="user-account-filter"
              value={accountFilter}
              onChange={(event) => setAccountFilter(event.target.value as typeof accountFilter)}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="all">All account types</option>
              <option value="local">Local</option>
              <option value="oidc">SSO-provisioned</option>
              <option value="hybrid">Local + SSO</option>
            </select>
            <label htmlFor="user-directory-search" className="sr-only">
              Search users
            </label>
            <input
              id="user-directory-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search users..."
              className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
            />
          </div>
        </div>

        <form
          id="create-user-form"
          className={`${createUserFormVisible ? 'grid' : 'hidden'} mt-4 gap-3 border-t border-slate/15 pt-4 sm:grid-cols-2 lg:grid-cols-4 dark:border-cyan-900/30`}
          onSubmit={onCreateSubmit}
        >
          <div className="sm:col-span-2 lg:col-span-4">
            <h3 className="text-sm font-semibold uppercase text-slate dark:text-slate-300">Create Local User</h3>
          </div>
          <div className="lg:col-span-2">
            <label htmlFor="create-user-email" className="text-sm font-semibold">
              Email
            </label>
            <input
              id="create-user-email"
              value={createForm.email}
              onChange={(event) =>
                setCreateForm((form) => ({
                  ...form,
                  email: event.target.value,
                }))
              }
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              type="email"
              autoComplete="off"
              required
            />
          </div>
          <div>
            <label htmlFor="create-user-password" className="text-sm font-semibold">
              Temporary password
            </label>
            <input
              id="create-user-password"
              value={createForm.password}
              onChange={(event) =>
                setCreateForm((form) => ({
                  ...form,
                  password: event.target.value,
                }))
              }
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              type="password"
              autoComplete="new-password"
              minLength={8}
              required
            />
          </div>
          <div>
            <label htmlFor="create-user-role" className="text-sm font-semibold">
              Role
            </label>
            <select
              id="create-user-role"
              value={createForm.role}
              onChange={(event) =>
                setCreateForm((form) => ({
                  ...form,
                  role: event.target.value as User['role'],
                }))
              }
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="viewer">viewer</option>
              <option value="analyst">analyst</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 sm:col-span-2 lg:col-span-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={createForm.is_active}
                onChange={(event) =>
                  setCreateForm((form) => ({
                    ...form,
                    is_active: event.target.checked,
                  }))
                }
              />
              Active
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={createForm.is_approved}
                onChange={(event) =>
                  setCreateForm((form) => ({
                    ...form,
                    is_approved: event.target.checked,
                  }))
                }
              />
              Approved
            </label>
            {createUser.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
                {resolveUsersMutationError(createUser.error)}
              </p>
            )}
          </div>
          <button
            className="rounded bg-ink px-3 py-2 text-white lg:justify-self-end dark:bg-cyan dark:text-[#053c2e]"
            disabled={createUser.isPending}
          >
            Review local user
          </button>
        </form>

        <details className="mt-3 rounded-lg border border-slate/20 bg-slate/5 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-white/[0.04]">
          <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900 dark:text-white">
            <span className="inline-flex items-center gap-2">
              <span>Role Definitions</span>
              <span className="text-xs font-normal text-slate dark:text-slate-300">
                Expand for admin, analyst, and viewer access boundaries
              </span>
            </span>
          </summary>
          <div className="mt-3 grid gap-3 lg:grid-cols-3">
            {ROLE_DEFINITIONS.map((entry) => (
              <div key={entry.role} className="border-l-2 border-slate/20 pl-3 dark:border-cyan-900/50">
                <h3 className="text-sm font-semibold uppercase text-slate-900 dark:text-white">{entry.role}</h3>
                <p className="mt-1 text-sm text-slate dark:text-slate-300">{entry.summary}</p>
                <ul className="mt-3 list-disc space-y-1 pl-4 text-sm text-slate-900 dark:text-slate-200">
                  {entry.capabilities.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </details>

        <div className="mt-3 space-y-2">
          {filteredUsers.map((user) => (
            <UserRow
              key={user.id}
              user={user}
              settingsDraft={settingsDraftsByUserId[user.id] ?? createUserSettingsDraft(user)}
              onSettingsDraftChange={(draft) =>
                setSettingsDraftsByUserId((current) => ({
                  ...current,
                  [user.id]: draft,
                }))
              }
              passwordDraft={passwordDraftsByUserId[user.id] ?? ''}
              onPasswordDraftChange={(draft) =>
                setPasswordDraftsByUserId((current) => ({
                  ...current,
                  [user.id]: draft,
                }))
              }
              actingUser={currentUserQuery.data ?? null}
              onSave={(body) => updateUser.mutate({ id: user.id, body })}
              saving={updateUser.isPending && updateUser.variables?.id === user.id}
              notice={rowNoticeByUserId[user.id] ?? null}
            />
          ))}

          {usersQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading users...</p>}
          {usersQuery.isError && (
            <p className="text-sm text-red-600 dark:text-red-300">{resolveUsersError(usersQuery.error)}</p>
          )}
          {!usersQuery.isLoading && !usersQuery.isError && filteredUsers.length === 0 && (
            <div className="rounded-lg border border-dashed border-slate/25 px-3 py-4 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
              No users match the current filters.
            </div>
          )}
        </div>
      </section>

      <ConfirmDialog
        open={Boolean(pendingCreateConfirmation)}
        title={pendingCreateConfirmation?.title ?? 'Create user account?'}
        description={pendingCreateConfirmation?.description}
        confirmLabel={pendingCreateConfirmation?.confirmLabel ?? 'Create user'}
        confirmTone={pendingCreateConfirmation?.confirmTone ?? 'primary'}
        onCancel={() => setPendingCreateConfirmation(null)}
        onConfirm={confirmCreateUser}
        confirmDisabled={!pendingCreateConfirmation}
        isConfirming={createUser.isPending}
      >
        {pendingCreateConfirmation && (
          <div className="space-y-3">
            <div className="space-y-1">
              <p className="font-semibold text-ink dark:text-white">{pendingCreateConfirmation.payload.email}</p>
              <p className="text-xs text-slate dark:text-white/70">
                Password set with {pendingCreateConfirmation.payload.password.length} characters
              </p>
            </div>
            <ul className="list-disc space-y-1 pl-4 text-sm text-slate-700 dark:text-white/80">
              {pendingCreateConfirmation.details.map((detail) => (
                <li key={detail}>{detail}</li>
              ))}
            </ul>
          </div>
        )}
      </ConfirmDialog>
      {confirmDiscardUnsavedUserSettingsChanges.discardDialog}
    </>
  )
}

function UserRow({
  user,
  settingsDraft,
  onSettingsDraftChange,
  passwordDraft,
  onPasswordDraftChange,
  actingUser,
  onSave,
  saving,
  notice,
}: {
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
}) {
  const roleInputId = `user-role-${user.id}`
  const passwordInputId = `user-reset-password-${user.id}`
  const passwordManagedLocally = user.password_managed_by === 'local'
  const roleManagedLocally = user.role_managed_by === 'local'
  const editableSettingsDraft = roleManagedLocally ? settingsDraft : { ...settingsDraft, role: user.role }
  const selfLockoutWarnings = resolveSelfLockoutWarnings(user, editableSettingsDraft, actingUser)
  const settingsConfirmation = buildUserSettingsConfirmation(user, editableSettingsDraft, actingUser)
  const passwordConfirmation = passwordManagedLocally ? buildPasswordResetConfirmation(user, passwordDraft) : null
  const [pendingConfirmationAction, setPendingConfirmationAction] = useState<'settings' | 'password' | null>(null)
  const [managementOpen, setManagementOpen] = useState(false)
  const pendingConfirmation =
    pendingConfirmationAction === 'password'
      ? passwordConfirmation
      : pendingConfirmationAction === 'settings'
        ? settingsConfirmation
        : null

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
      <div className="rounded border border-slate/20 p-2.5 sm:p-3 dark:border-cyan-900/40">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="break-all font-semibold sm:break-normal">{user.email}</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <span className={`tl-chip ${user.provisioning_source === 'oidc' ? 'tl-chip-info' : 'tl-chip-neutral'}`}>
                {resolveAccountLabel(user)}
              </span>
              <span className="tl-chip tl-chip-neutral">{user.role}</span>
              <span className={`tl-chip ${user.is_active ? 'tl-chip-success' : 'tl-chip-neutral'}`}>
                {user.is_active ? 'Active' : 'Inactive'}
              </span>
              {!user.is_approved && <span className="tl-chip tl-chip-warning">Pending approval</span>}
              {user.authentication_methods.length === 0 && (
                <span className="tl-chip tl-chip-warning">No sign-in method</span>
              )}
            </div>
            <p className="mt-1.5 text-xs text-slate dark:text-slate-300">
              Created {formatDateTime(user.created_at)}
              {user.approved_at ? ` · Approved ${formatDateTime(user.approved_at)}` : ''}
            </p>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              Sign-in: {formatAuthenticationMethods(user)}
              {user.oidc_provider_name ? ` · Provider: ${user.oidc_provider_name}` : ''}
              {user.oidc_last_login_at ? ` · Last SSO sign-in ${formatDateTime(user.oidc_last_login_at)}` : ''}
            </p>
          </div>
          <button
            type="button"
            className="shrink-0 rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold dark:border-cyan-900/40"
            aria-expanded={managementOpen}
            aria-controls={`user-settings-${user.id} user-management-${user.id}`}
            onClick={() => setManagementOpen((current) => !current)}
          >
            {managementOpen ? 'Close' : 'Manage'}
          </button>
        </div>

        <div
          id={`user-management-${user.id}`}
          className={`${managementOpen ? 'block' : 'hidden'} mt-3 border-t border-slate/15 pt-3 dark:border-cyan-900/30`}
        >
          <div
            id={`user-settings-${user.id}`}
            className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(170px,1fr)_auto_auto_auto] xl:items-end"
          >
            <div>
              <p className="text-xs font-semibold uppercase text-slate dark:text-slate-300">Role</p>
              {roleManagedLocally ? (
                <>
                  <label htmlFor={roleInputId} className="sr-only">
                    Role for {user.email}
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
                    <option value="viewer">viewer</option>
                    <option value="analyst">analyst</option>
                    <option value="admin">admin</option>
                  </select>
                </>
              ) : (
                <div className="mt-1 text-sm">
                  <p className="font-semibold">{user.role}</p>
                  <p className="text-xs text-slate dark:text-slate-300">
                    Managed by {user.oidc_provider_name || 'SSO'}
                  </p>
                </div>
              )}
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={editableSettingsDraft.isActive}
                onChange={(event) =>
                  onSettingsDraftChange({
                    ...editableSettingsDraft,
                    isActive: event.target.checked,
                  })
                }
              />
              Active
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={editableSettingsDraft.isApproved}
                onChange={(event) =>
                  onSettingsDraftChange({
                    ...editableSettingsDraft,
                    isApproved: event.target.checked,
                  })
                }
              />
              Approved
            </label>
            <button
              className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40"
              disabled={saving || !settingsConfirmation}
              onClick={() => setPendingConfirmationAction('settings')}
            >
              Review changes
            </button>
          </div>

          <div className="mt-3 border-t border-slate/15 pt-3 dark:border-cyan-900/30">
            <p className="text-xs font-semibold uppercase text-slate dark:text-slate-300">Authentication</p>
            {passwordManagedLocally ? (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <label htmlFor={passwordInputId} className="sr-only">
                  New password for {user.email}
                </label>
                <input
                  id={passwordInputId}
                  type="password"
                  placeholder="New password (min 8 chars)"
                  value={passwordDraft}
                  onChange={(event) => onPasswordDraftChange(event.target.value)}
                  className="w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
                />
                <button
                  className="rounded border border-slate/30 px-3 py-1.5 text-sm dark:border-cyan-900/40"
                  disabled={saving || !passwordConfirmation}
                  onClick={() => setPendingConfirmationAction('password')}
                >
                  Review password reset
                </button>
              </div>
            ) : (
              <p className="mt-1 text-sm text-slate dark:text-slate-300">
                Credentials are managed by {user.oidc_provider_name || 'the identity provider'}.
              </p>
            )}
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
              <p className="font-semibold text-ink dark:text-white">{user.email}</p>
              <p className="text-xs text-slate dark:text-white/70">Role: {user.role}</p>
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

function resolveUsersError(error: unknown): string {
  return resolveApiErrorMessage(error, 'User directory could not be loaded')
}

function resolveAccountCategory(user: AdminUser): 'local' | 'oidc' | 'hybrid' {
  if (user.provisioning_source === 'oidc') {
    return 'oidc'
  }
  return user.authentication_methods.includes('oidc') ? 'hybrid' : 'local'
}

function resolveAccountLabel(user: AdminUser): string {
  const category = resolveAccountCategory(user)
  if (category === 'oidc') {
    return 'SSO-provisioned'
  }
  if (category === 'hybrid') {
    return 'Local + SSO'
  }
  return 'Local'
}

function formatAuthenticationMethods(user: AdminUser): string {
  if (user.authentication_methods.length === 0) {
    return 'None'
  }
  return user.authentication_methods.map((method) => (method === 'oidc' ? 'SSO' : 'Password')).join(' + ')
}

function resolveUsersMutationError(error: unknown): string {
  return resolveApiErrorMessage(error, 'User changes could not be saved')
}

function hasDirtyUserSettingsDrafts(
  draftsByUserId: Record<string, UserSettingsDraft>,
  baselinesByUserId: Record<string, UserSettingsDraft>,
) {
  return Object.entries(draftsByUserId).some(([userId, draft]) => {
    const baseline = baselinesByUserId[userId]
    if (!baseline) {
      return false
    }

    return (
      draft.role !== baseline.role || draft.isActive !== baseline.isActive || draft.isApproved !== baseline.isApproved
    )
  })
}

function isCreateUserFormDirty(form: UserCreateRequest) {
  return (
    form.email !== DEFAULT_CREATE_USER_FORM.email ||
    form.password !== DEFAULT_CREATE_USER_FORM.password ||
    form.role !== DEFAULT_CREATE_USER_FORM.role ||
    form.is_active !== DEFAULT_CREATE_USER_FORM.is_active ||
    form.is_approved !== DEFAULT_CREATE_USER_FORM.is_approved
  )
}
