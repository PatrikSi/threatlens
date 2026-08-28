import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useAuth } from '../components/AuthContext'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { usePendingEntityActions } from '../hooks/usePendingEntityActions'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import {
  AdminMFAResetResponse,
  AdminUser,
  AdminUserUpdateResponse,
  AuthSessionListResponse,
  MFAStatusResponse,
  User,
  UserCreateRequest,
  UserDirectoryResponse,
  UserUpdateRequest,
} from '../types/api'
import { AdminMFAResetDialog } from './AdminMFAResetDialog'
import {
  resolveOIDCReauthNotice,
  resolveOIDCReauthStartError,
  type OIDCCallbackNotice,
} from './oidcCallbackMessages'
import {
  beginOIDCReauthentication,
  readOIDCReauthNavigationState,
} from './oidcReauthentication'
import {
  UserDirectoryHeader,
  UserDirectoryQueryState,
  UserRoleDefinitions,
} from './UserDirectoryChrome'
import { UserDirectoryRow } from './UserDirectoryRow'
import {
  buildCreateUserConfirmation,
  createUserSettingsDraft,
  CreateUserConfirmationState,
  isUserSettingsDraftEqual,
  syncUserSettingsDrafts,
  UserSettingsDraft,
  UserSettingsDraftConflict,
} from './userSettingsDraft'
import {
  buildUserDirectoryPath,
  formatUserUpdateSuccess,
  hasDirtyUserSettingsDrafts,
  isCreateUserFormDirty,
  isUserSecurityVersionConflict,
  resolveCredentialManagementSource,
  resolveUsersMutationError,
  type UserProvisioningFilter,
  type UserRoleFilter,
} from './userDirectoryModel'

const DEFAULT_CREATE_USER_FORM: UserCreateRequest = {
  email: '',
  password: '',
  role: 'viewer',
  is_active: true,
  is_approved: true,
}

const EMPTY_MFA_RESET_DRAFT = { reason: '', currentPassword: '', code: '' }
const CREATE_USER_MUTATION_KEY = ['users', 'create'] as const
const UPDATE_USER_MUTATION_KEY = ['users', 'update'] as const
const MFA_RESET_MUTATION_KEY = ['users', 'mfa-reset'] as const
const DIRECTORY_PAGE_SIZE = 100

type DirectoryNotice = {
  tone: 'success' | 'error'
  message: string
}

function forgetCredentialMutation(
  queryClient: QueryClient,
  mutationKey: readonly unknown[],
  reset: () => void,
) {
  reset()
  const mutationCache = queryClient.getMutationCache()
  mutationCache.findAll({ mutationKey, exact: true }).forEach((mutation) => {
    if (mutation.state.status !== 'pending') mutationCache.remove(mutation)
  })
}

function forgetCredentialMutationAfterSettlement(
  queryClient: QueryClient,
  mutationKey: readonly unknown[],
) {
  window.setTimeout(() => {
    const mutationCache = queryClient.getMutationCache()
    mutationCache.findAll({ mutationKey, exact: true }).forEach((mutation) => {
      if (mutation.state.status !== 'pending') mutationCache.remove(mutation)
    })
  }, 0)
}

export function UsersPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const { markLoggedOut } = useAuth()
  const currentUserQuery = useCurrentUser()
  const userUpdatePending = usePendingEntityActions()
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState<UserRoleFilter>('all')
  const [accountFilter, setAccountFilter] =
    useState<UserProvisioningFilter>('all')
  const [directoryOffset, setDirectoryOffset] = useState(0)
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
  const [settingsDraftsByUserId, setSettingsDraftsByUserId] = useState<
    Record<string, UserSettingsDraft>
  >({})
  const settingsDraftsByUserIdRef = useRef<Record<string, UserSettingsDraft>>(
    {},
  )
  const [settingsConflictsByUserId, setSettingsConflictsByUserId] = useState<
    Record<string, UserSettingsDraftConflict>
  >({})
  const settingsConflictsByUserIdRef = useRef<
    Record<string, UserSettingsDraftConflict>
  >({})
  const [passwordDraftsByUserId, setPasswordDraftsByUserId] = useState<
    Record<string, string>
  >({})
  const settingsDraftBaselinesByUserIdRef = useRef<
    Record<string, UserSettingsDraft>
  >({})
  const knownUserEmailsByIdRef = useRef<Record<string, string>>({})
  const [createForm, setCreateForm] = useState<UserCreateRequest>(
    DEFAULT_CREATE_USER_FORM,
  )
  const [pendingCreateConfirmation, setPendingCreateConfirmation] =
    useState<CreateUserConfirmationState | null>(null)
  const [createUserOpen, setCreateUserOpen] = useState(false)
  const [mfaResetTarget, setMfaResetTarget] = useState<AdminUser | null>(null)
  const [mfaResetDraft, setMfaResetDraft] = useState(EMPTY_MFA_RESET_DRAFT)
  const [directoryNotice, setDirectoryNotice] =
    useState<DirectoryNotice | null>(null)
  const [createUserError, setCreateUserError] = useState('')
  const [mfaResetError, setMfaResetError] = useState('')
  const [reauthNavigation] = useState(() =>
    readOIDCReauthNavigationState(location.state, 'admin_mfa_reset'),
  )
  const reauthContinuationHandledRef = useRef(false)
  const reauthNotice: OIDCCallbackNotice | null = reauthNavigation
    ? resolveOIDCReauthNotice(reauthNavigation.result)
    : null

  const directoryPath = useMemo(
    () =>
      buildUserDirectoryPath({
        search,
        role: roleFilter,
        provisioningSource: accountFilter,
        offset: directoryOffset,
        limit: DIRECTORY_PAGE_SIZE,
      }),
    [accountFilter, directoryOffset, roleFilter, search],
  )
  const usersQuery = useQuery({
    queryKey: [
      'users',
      'directory',
      search,
      roleFilter,
      accountFilter,
      directoryOffset,
    ],
    queryFn: ({ signal }) =>
      apiFetch<UserDirectoryResponse>(directoryPath, { signal }),
  })
  const ownMfaQuery = useQuery({
    queryKey: ['auth', 'security', 'mfa'],
    queryFn: () => apiFetch<MFAStatusResponse>('/auth/security/mfa'),
    enabled: Boolean(mfaResetTarget),
  })
  const currentSessionsQuery = useQuery({
    queryKey: ['auth', 'security', 'sessions'],
    queryFn: () => apiFetch<AuthSessionListResponse>('/auth/security/sessions'),
    enabled: Boolean(mfaResetTarget),
  })
  const oidcReauthentication = useMutation({
    mutationKey: ['auth', 'oidc', 'reauth', 'admin-mfa-reset'],
    mutationFn: ({ target, reason }: { target: AdminUser; reason: string }) =>
      beginOIDCReauthentication({
        returnPath: '/settings/users',
        purpose: 'admin_mfa_reset',
        context: {
          targetUserId: target.id,
          targetEmail: target.email,
          reason: reason.trim(),
        },
      }),
    onMutate: () => setMfaResetError(''),
    onError: (error) => setMfaResetError(resolveOIDCReauthStartError(error)),
  })

  const createUser = useMutation({
    mutationKey: CREATE_USER_MUTATION_KEY,
    gcTime: 0,
    mutationFn: (payload: UserCreateRequest) =>
      apiFetch<AdminUser>('/users', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onMutate: () => setCreateUserError(''),
    onSuccess: () => {
      setCreateForm(DEFAULT_CREATE_USER_FORM)
      setCreateUserOpen(false)
      setDirectoryOffset(0)
      void queryClient.invalidateQueries({ queryKey: ['users', 'directory'] })
    },
    onError: (error) => setCreateUserError(resolveUsersMutationError(error)),
    onSettled: () =>
      forgetCredentialMutationAfterSettlement(
        queryClient,
        CREATE_USER_MUTATION_KEY,
      ),
  })

  const updateUser = useMutation({
    mutationKey: UPDATE_USER_MUTATION_KEY,
    gcTime: 0,
    mutationFn: (payload: { id: string; body: UserUpdateRequest }) =>
      apiFetch<AdminUserUpdateResponse>(`/users/${payload.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload.body),
      }),
    onMutate: (payload) => {
      userUpdatePending.begin('update', payload.id)
      setRowNoticeByUserId((current) => {
        const next = { ...current }
        delete next[payload.id]
        return next
      })
    },
    onSuccess: (user, payload) => {
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
          message: formatUserUpdateSuccess(user, payload.body),
          action: payload.body.password ? 'password' : 'settings',
        },
      }))
      void queryClient.invalidateQueries({ queryKey: ['users', 'directory'] })
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
      if (isUserSecurityVersionConflict(error)) {
        void usersQuery.refetch()
      }
    },
    onSettled: (_data, _error, payload) => {
      userUpdatePending.finish('update', payload.id)
      forgetCredentialMutationAfterSettlement(
        queryClient,
        UPDATE_USER_MUTATION_KEY,
      )
    },
  })

  const resetUserMfa = useMutation({
    mutationKey: MFA_RESET_MUTATION_KEY,
    gcTime: 0,
    mutationFn: ({
      target,
      draft,
    }: {
      target: AdminUser
      draft: typeof EMPTY_MFA_RESET_DRAFT
    }) =>
      apiFetch<AdminMFAResetResponse>(`/users/${target.id}/mfa/reset`, {
        method: 'POST',
        body: JSON.stringify({
          reason: draft.reason.trim(),
          ...(draft.currentPassword
            ? { current_password: draft.currentPassword }
            : {}),
          ...(draft.code.trim() ? { code: draft.code.trim() } : {}),
        }),
      }),
    onMutate: () => setMfaResetError(''),
    onSuccess: async (result, { target }) => {
      setMfaResetTarget(null)
      setMfaResetDraft(EMPTY_MFA_RESET_DRAFT)
      if (target.id === currentUserQuery.data?.id) {
        markLoggedOut()
        navigate('/login', {
          replace: true,
          state: {
            authMessage:
              'Your MFA enrollment was reset. Sign in again to continue.',
          },
        })
        return
      }
      setDirectoryNotice({
        tone: 'success',
        message: `MFA reset for ${target.email}. ${result.revoked_auth_sessions} browser session${result.revoked_auth_sessions === 1 ? '' : 's'} and ${result.revoked_api_tokens} API token${result.revoked_api_tokens === 1 ? '' : 's'} revoked.`,
      })
      await queryClient.invalidateQueries({ queryKey: ['users', 'directory'] })
    },
    onError: (error) =>
      setMfaResetError(resolveApiErrorMessage(error, 'MFA could not be reset')),
    onSettled: () =>
      forgetCredentialMutationAfterSettlement(
        queryClient,
        MFA_RESET_MUTATION_KEY,
      ),
  })

  const filteredUsers = useMemo(
    () => usersQuery.data?.users ?? [],
    [usersQuery.data?.users],
  )

  useEffect(() => {
    settingsDraftsByUserIdRef.current = settingsDraftsByUserId
  }, [settingsDraftsByUserId])

  useEffect(() => {
    settingsConflictsByUserIdRef.current = settingsConflictsByUserId
  }, [settingsConflictsByUserId])

  useEffect(() => {
    const users = usersQuery.data?.users ?? []
    for (const user of users) {
      knownUserEmailsByIdRef.current[user.id] = user.email
    }
    const synced = syncUserSettingsDrafts(
      users,
      settingsDraftsByUserIdRef.current,
      settingsDraftBaselinesByUserIdRef.current,
    )
    for (const user of users) {
      if (user.role_managed_by === 'oidc') {
        synced.drafts[user.id].role = user.role
        const conflict = synced.conflicts[user.id]
        if (conflict) {
          conflict.serverDraft.role = user.role
          conflict.reappliedDraft.role = user.role
          conflict.overlappingFields = conflict.overlappingFields.filter(
            (field) => field.field !== 'role',
          )
          if (!conflict.overlappingFields.length)
            delete synced.conflicts[user.id]
        }
      }
    }
    for (const [userId, conflict] of Object.entries(
      settingsConflictsByUserIdRef.current,
    )) {
      if (
        !synced.conflicts[userId] &&
        JSON.stringify(synced.baselines[userId]) ===
          JSON.stringify(conflict.serverDraft) &&
        JSON.stringify(synced.drafts[userId]) ===
          JSON.stringify(conflict.reappliedDraft)
      ) {
        synced.conflicts[userId] = conflict
      }
    }
    settingsDraftBaselinesByUserIdRef.current = synced.baselines
    settingsDraftsByUserIdRef.current = synced.drafts
    settingsConflictsByUserIdRef.current = synced.conflicts
    setSettingsDraftsByUserId(synced.drafts)
    setSettingsConflictsByUserId(synced.conflicts)
    setPasswordDraftsByUserId((current) => {
      const visibleUsersById = new Map(users.map((user) => [user.id, user]))
      const next = Object.fromEntries(
        Object.entries(current).filter(
          ([userId, draft]) => {
            if (!draft.length) return false
            const visibleUser = visibleUsersById.get(userId)
            return (
              !visibleUser ||
              resolveCredentialManagementSource(visibleUser) === 'local'
            )
          },
        ),
      ) as Record<string, string>
      return next
    })
  }, [usersQuery.data?.users])

  useEffect(() => {
    const context = reauthNavigation?.context
    if (reauthContinuationHandledRef.current || !reauthNavigation) return
    reauthContinuationHandledRef.current = true
    navigate(`${location.pathname}${location.search}`, {
      replace: true,
      state: null,
    })
    if (!context?.targetUserId) {
      setDirectoryNotice({
        tone: 'error',
        message:
          'SSO verification returned without a recoverable MFA-reset target. Start the reset again from the user directory.',
      })
      return
    }
    let cancelled = false
    const targetPath = `/users/${encodeURIComponent(context.targetUserId)}`
    void apiFetch<AdminUser>(targetPath)
      .then((target) => {
        if (cancelled) return
        setMfaResetTarget(target)
        setMfaResetDraft({
          ...EMPTY_MFA_RESET_DRAFT,
          reason: context.reason ?? '',
        })
      })
      .catch((error) => {
        if (cancelled) return
        const targetDescription = context.targetEmail
          ? ` for ${context.targetEmail}`
          : ''
        setDirectoryNotice({
          tone: 'error',
          message: resolveApiErrorMessage(
            error,
            `The MFA-reset target${targetDescription} could not be restored after SSO verification`,
            {
              retryGuidance:
                'The account may have been removed or your access may have changed. Start the reset again from the user directory.',
            },
          ),
        })
      })
    return () => {
      cancelled = true
    }
  }, [
    location.pathname,
    location.search,
    navigate,
    reauthNavigation,
  ])

  const hasUnsavedUserSettingsChanges = useMemo(
    () =>
      hasDirtyUserSettingsDrafts(
        settingsDraftsByUserId,
        settingsDraftBaselinesByUserIdRef.current,
      ),
    [settingsDraftsByUserId],
  )
  const hasUnsavedPasswordDrafts = useMemo(
    () =>
      Object.values(passwordDraftsByUserId).some((value) => value.length > 0),
    [passwordDraftsByUserId],
  )
  const hasUnsavedCreateUserChanges = isCreateUserFormDirty(createForm)
  const hiddenUserDrafts = useMemo(() => {
    const visibleUserIds = new Set(filteredUsers.map((user) => user.id))
    const dirtyUserIds = new Set<string>()
    for (const [userId, draft] of Object.entries(settingsDraftsByUserId)) {
      const baseline = settingsDraftBaselinesByUserIdRef.current[userId]
      if (baseline && !isUserSettingsDraftEqual(draft, baseline)) {
        dirtyUserIds.add(userId)
      }
    }
    for (const [userId, password] of Object.entries(passwordDraftsByUserId)) {
      if (password.length > 0) dirtyUserIds.add(userId)
    }
    return Array.from(dirtyUserIds)
      .filter((userId) => !visibleUserIds.has(userId))
      .map((userId) => ({
        userId,
        email: knownUserEmailsByIdRef.current[userId] ?? null,
      }))
  }, [filteredUsers, passwordDraftsByUserId, settingsDraftsByUserId])
  const firstHiddenUserDraft = hiddenUserDrafts[0] ?? null
  const confirmDiscardUnsavedUserSettingsChanges = useUnsavedChangesWarning(
    hasUnsavedUserSettingsChanges ||
      hasUnsavedPasswordDrafts ||
      hasUnsavedCreateUserChanges,
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

  const updateCreateForm = (
    updater: (current: UserCreateRequest) => UserCreateRequest,
  ) => {
    setCreateUserError('')
    setCreateForm(updater)
  }

  const showHiddenUserDraft = (email: string | null) => {
    setSearch(email ?? '')
    setRoleFilter('all')
    setAccountFilter('all')
    setDirectoryOffset(0)
  }

  const discardHiddenUserDraft = (userId: string) => {
    if (userUpdatePending.isPending('update', userId)) return
    const baseline = settingsDraftBaselinesByUserIdRef.current[userId]
    const hasPasswordDraft = Boolean(passwordDraftsByUserId[userId])
    setSettingsDraftsByUserId((current) => {
      const next = { ...current }
      if (baseline) next[userId] = baseline
      else delete next[userId]
      settingsDraftsByUserIdRef.current = next
      return next
    })
    setPasswordDraftsByUserId((current) => {
      const next = { ...current }
      delete next[userId]
      return next
    })
    setSettingsConflictsByUserId((current) => {
      const next = { ...current }
      delete next[userId]
      settingsConflictsByUserIdRef.current = next
      return next
    })
    if (hasPasswordDraft) {
      forgetCredentialMutation(
        queryClient,
        UPDATE_USER_MUTATION_KEY,
        updateUser.reset,
      )
    }
  }

  const directoryIsUnfiltered =
    !search.trim() && roleFilter === 'all' && accountFilter === 'all'
  const createUserFormVisible =
    usersQuery.isSuccess &&
    (createUserOpen || (directoryIsUnfiltered && usersQuery.data.total === 0))

  return (
    <>
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <UserDirectoryHeader
          data={usersQuery.data}
          filteredCount={filteredUsers.length}
          isLoading={usersQuery.isLoading}
          isError={usersQuery.isError}
          isSuccess={usersQuery.isSuccess}
          createUserFormVisible={createUserFormVisible}
          hasCreateUserDraft={hasUnsavedCreateUserChanges}
          search={search}
          roleFilter={roleFilter}
          accountFilter={accountFilter}
          onToggleCreate={() => setCreateUserOpen((current) => !current)}
          onSearchChange={(value) => {
            setSearch(value)
            setDirectoryOffset(0)
          }}
          onRoleFilterChange={(value) => {
            setRoleFilter(value)
            setDirectoryOffset(0)
          }}
          onAccountFilterChange={(value) => {
            setAccountFilter(value)
            setDirectoryOffset(0)
          }}
        />

        {directoryNotice && (
          <p
            role={directoryNotice.tone === 'error' ? 'alert' : 'status'}
            aria-live={directoryNotice.tone === 'error' ? 'assertive' : 'polite'}
            aria-atomic="true"
            className={`mt-3 rounded border px-3 py-2 text-sm ${
              directoryNotice.tone === 'error'
                ? 'border-red-300/60 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200'
                : 'border-green-300/60 bg-green-50 text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-200'
            }`}
          >
            {directoryNotice.message}
          </p>
        )}

        {firstHiddenUserDraft && (
          <div
            className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
          >
            <p role="status" aria-live="polite">
              {hiddenUserDrafts.length} unsaved account draft
              {hiddenUserDrafts.length === 1 ? ' is' : 's are'} hidden by the current filters or
              directory page.
            </p>
            <div className="mt-2 grid gap-2 sm:flex sm:flex-wrap">
              <button
                type="button"
                className="min-h-11 rounded border border-current px-3 py-2 font-semibold sm:min-h-0 sm:py-1.5"
                onClick={() => showHiddenUserDraft(firstHiddenUserDraft.email)}
              >
                {firstHiddenUserDraft.email
                  ? `Show draft for ${firstHiddenUserDraft.email}`
                  : 'Show hidden account draft'}
              </button>
              <button
                type="button"
                className="min-h-11 rounded border border-current px-3 py-2 font-semibold sm:min-h-0 sm:py-1.5"
                onClick={() => discardHiddenUserDraft(firstHiddenUserDraft.userId)}
                disabled={userUpdatePending.isPending(
                  'update',
                  firstHiddenUserDraft.userId,
                )}
                title={
                  userUpdatePending.isPending(
                    'update',
                    firstHiddenUserDraft.userId,
                  )
                    ? 'Wait for the in-progress account update to finish before discarding this draft.'
                    : undefined
                }
              >
                {userUpdatePending.isPending(
                  'update',
                  firstHiddenUserDraft.userId,
                )
                  ? 'Account update in progress...'
                  : 'Discard hidden draft'}
              </button>
            </div>
          </div>
        )}

        {usersQuery.isSuccess && (
          <form
            id="create-user-form"
            className={`${createUserFormVisible ? 'grid' : 'hidden'} mt-4 gap-3 border-t border-slate/15 pt-4 sm:grid-cols-2 lg:grid-cols-4 dark:border-cyan-900/30`}
            onSubmit={onCreateSubmit}
          >
            <div className="sm:col-span-2 lg:col-span-4">
              <h3 className="text-sm font-semibold uppercase text-slate dark:text-slate-300">
                Create Local User
              </h3>
            </div>
            <div className="lg:col-span-2">
              <label
                htmlFor="create-user-email"
                className="text-sm font-semibold"
              >
                Email
              </label>
              <input
                id="create-user-email"
                value={createForm.email}
                onChange={(event) =>
                  updateCreateForm((form) => ({
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
              <label
                htmlFor="create-user-password"
                className="text-sm font-semibold"
              >
                Initial password
              </label>
              <input
                id="create-user-password"
                value={createForm.password}
                onChange={(event) =>
                  updateCreateForm((form) => ({
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
              <p className="mt-1 text-xs text-slate dark:text-slate-300">
                This password remains valid until the user or an administrator
                changes it.
              </p>
            </div>
            <div>
              <label
                htmlFor="create-user-role"
                className="text-sm font-semibold"
              >
                Role
              </label>
              <select
                id="create-user-role"
                value={createForm.role}
                onChange={(event) =>
                  updateCreateForm((form) => ({
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
              <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
                <input
                  type="checkbox"
                  className="h-5 w-5 sm:h-4 sm:w-4"
                  checked={createForm.is_active}
                  onChange={(event) =>
                    updateCreateForm((form) => ({
                      ...form,
                      is_active: event.target.checked,
                    }))
                  }
                />
                Active
              </label>
              <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
                <input
                  type="checkbox"
                  className="h-5 w-5 sm:h-4 sm:w-4"
                  checked={createForm.is_approved}
                  onChange={(event) =>
                    updateCreateForm((form) => ({
                      ...form,
                      is_approved: event.target.checked,
                    }))
                  }
                />
                Approved
              </label>
              {createUserError && (
                <p
                  role="alert"
                  aria-live="assertive"
                  aria-atomic="true"
                  className="text-sm text-red-600"
                >
                  {createUserError}
                </p>
              )}
            </div>
            <button
              className="rounded bg-ink px-3 py-2 text-white lg:justify-self-end dark:bg-cyan dark:text-[#053c2e]"
              disabled={createUser.isPending}
              aria-label={
                createForm.email
                  ? `Review creation of local user ${createForm.email}`
                  : 'Review local user creation'
              }
            >
              Review local user
            </button>
          </form>
        )}

        <UserRoleDefinitions />

        <div className="mt-3 space-y-2">
          {usersQuery.isSuccess &&
            filteredUsers.map((user) => (
              <UserDirectoryRow
                key={user.id}
                user={user}
                settingsDraft={
                  settingsDraftsByUserId[user.id] ??
                  createUserSettingsDraft(user)
                }
                onSettingsDraftChange={(draft) => {
                  settingsDraftsByUserIdRef.current = {
                    ...settingsDraftsByUserIdRef.current,
                    [user.id]: draft,
                  }
                  setSettingsDraftsByUserId((current) => ({
                    ...current,
                    [user.id]: draft,
                  }))
                }}
                passwordDraft={passwordDraftsByUserId[user.id] ?? ''}
                onPasswordDraftChange={(draft) => {
                  setPasswordDraftsByUserId((current) => ({
                    ...current,
                    [user.id]: draft,
                  }))
                  if (!draft) {
                    forgetCredentialMutation(
                      queryClient,
                      UPDATE_USER_MUTATION_KEY,
                      updateUser.reset,
                    )
                  }
                }}
                actingUser={currentUserQuery.data ?? null}
                onSave={(body) => updateUser.mutate({ id: user.id, body })}
                saving={userUpdatePending.isPending('update', user.id)}
                notice={rowNoticeByUserId[user.id] ?? null}
                settingsConflict={settingsConflictsByUserId[user.id] ?? null}
                onUseServerSettings={() => {
                  const conflict = settingsConflictsByUserId[user.id]
                  if (!conflict) return
                  settingsDraftsByUserIdRef.current = {
                    ...settingsDraftsByUserIdRef.current,
                    [user.id]: conflict.serverDraft,
                  }
                  setSettingsDraftsByUserId((current) => ({
                    ...current,
                    [user.id]: conflict.serverDraft,
                  }))
                  setSettingsConflictsByUserId((current) => {
                    const next = { ...current }
                    delete next[user.id]
                    settingsConflictsByUserIdRef.current = next
                    return next
                  })
                }}
                onReapplySettings={() => {
                  const conflict = settingsConflictsByUserId[user.id]
                  if (!conflict) return
                  settingsDraftsByUserIdRef.current = {
                    ...settingsDraftsByUserIdRef.current,
                    [user.id]: conflict.reappliedDraft,
                  }
                  setSettingsDraftsByUserId((current) => ({
                    ...current,
                    [user.id]: conflict.reappliedDraft,
                  }))
                  setSettingsConflictsByUserId((current) => {
                    const next = { ...current }
                    delete next[user.id]
                    settingsConflictsByUserIdRef.current = next
                    return next
                  })
                }}
                onResetMfa={() => {
                  setDirectoryNotice(null)
                  setMfaResetError('')
                  forgetCredentialMutation(
                    queryClient,
                    MFA_RESET_MUTATION_KEY,
                    resetUserMfa.reset,
                  )
                  setMfaResetDraft(EMPTY_MFA_RESET_DRAFT)
                  setMfaResetTarget(user)
                }}
              />
            ))}

          <UserDirectoryQueryState
            data={usersQuery.data}
            filteredUsers={filteredUsers}
            directoryIsUnfiltered={directoryIsUnfiltered}
            isLoading={usersQuery.isLoading}
            isError={usersQuery.isError}
            isFetching={usersQuery.isFetching}
            error={usersQuery.error}
            onRetry={() => void usersQuery.refetch()}
            onOffsetChange={setDirectoryOffset}
          />
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
              <p className="font-semibold text-ink dark:text-white">
                {pendingCreateConfirmation.payload.email}
              </p>
              <p className="text-xs text-slate dark:text-white/70">
                Password set with{' '}
                {pendingCreateConfirmation.payload.password.length} characters
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
      <AdminMFAResetDialog
        target={mfaResetTarget}
        draft={mfaResetDraft}
        ownMfa={ownMfaQuery.data}
        currentAuthentication={currentUserQuery.data?.authentication}
        ownMfaLoading={ownMfaQuery.isLoading}
        ownMfaError={ownMfaQuery.error}
        sessions={currentSessionsQuery.data}
        sessionsLoading={currentSessionsQuery.isLoading}
        sessionsFetching={currentSessionsQuery.isFetching}
        sessionsError={currentSessionsQuery.error}
        reauthNotice={reauthNotice}
        reauthPending={oidcReauthentication.isPending}
        onRetryOwnMfa={() => void ownMfaQuery.refetch()}
        onRetrySessions={() => void currentSessionsQuery.refetch()}
        onStartOIDCReauth={() => {
          if (!mfaResetTarget) return
          oidcReauthentication.mutate({
            target: mfaResetTarget,
            reason: mfaResetDraft.reason,
          })
        }}
        errorMessage={mfaResetError}
        pending={resetUserMfa.isPending}
        onDraftChange={(draft) => {
          setMfaResetDraft(draft)
          setMfaResetError('')
        }}
        onCancel={() => {
          if (resetUserMfa.isPending) return
          setMfaResetTarget(null)
          setMfaResetDraft(EMPTY_MFA_RESET_DRAFT)
          setMfaResetError('')
          forgetCredentialMutation(
            queryClient,
            MFA_RESET_MUTATION_KEY,
            resetUserMfa.reset,
          )
          oidcReauthentication.reset()
        }}
        onConfirm={() => {
          if (mfaResetTarget)
            resetUserMfa.mutate({
              target: mfaResetTarget,
              draft: mfaResetDraft,
            })
        }}
      />
      {confirmDiscardUnsavedUserSettingsChanges.discardDialog}
    </>
  )
}
