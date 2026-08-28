// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type {
  AdminUser,
  AuthSessionListResponse,
  MFAStatusResponse,
} from '../types/api'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const usersPageDomMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  userLookupResult: null as AdminUser | null,
  userLookupError: null as unknown,
  mutationRecords: [] as Array<{
    mutationKey: unknown[]
    state: {
      status: 'pending' | 'success' | 'error'
      variables: unknown
      error: unknown
    }
  }>,
  mutationGcTimes: {} as Record<string, number | undefined>,
  nextMutationErrors: {} as Record<string, unknown>,
  mutationCache: {
    findAll: ({ mutationKey }: { mutationKey: readonly unknown[] }) =>
      usersPageDomMocks.mutationRecords.filter(
        (record) =>
          JSON.stringify(record.mutationKey) === JSON.stringify(mutationKey),
      ),
    remove: (record: unknown) => {
      const index = usersPageDomMocks.mutationRecords.indexOf(record as never)
      if (index >= 0) usersPageDomMocks.mutationRecords.splice(index, 1)
    },
  },
  queryClient: {
    invalidateQueries: vi.fn(),
    getMutationCache: () => usersPageDomMocks.mutationCache,
  },
  queryRefetch: vi.fn(),
  directoryStatus: 'success' as 'loading' | 'error' | 'success',
  directoryError: null as unknown,
  directoryTotal: null as number | null,
  directoryOffset: 0,
  directoryLimit: 100,
  directoryHasMore: false,
  mutate: vi.fn(),
  navigate: vi.fn(),
  markLoggedOut: vi.fn(),
  mfaResetPayload: null as null | {
    target: AdminUser
    draft: { reason: string; currentPassword: string; code: string }
  },
  ownMfaData: {
    local_mfa_available: true,
    managed_by: 'local' as const,
    enabled: false,
    confirmed_at: null,
    recovery_codes_remaining: 0,
  } as MFAStatusResponse,
  ownMfaError: null as unknown,
  currentSessionsData: {
    sessions: [
      {
        id: 'session-admin-1',
        current: true,
        auth_method: 'local' as const,
        mfa_method: 'totp' as const,
        client_ip: '192.0.2.10',
        user_agent: 'Test browser',
        authenticated_at: '2026-08-27T10:00:00Z',
        last_seen_at: '2026-08-27T10:00:00Z',
        idle_expires_at: '2026-08-28T10:00:00Z',
        absolute_expires_at: '2026-09-03T10:00:00Z',
        revoked_at: null,
        revoked_reason: null,
      },
    ],
    active_count: 1,
    history_truncated: false,
  } as AuthSessionListResponse,
  pendingUserUpdates: {} as Record<string, (error?: unknown) => void>,
  locationState: null as unknown,
  currentUser: {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
      password_login_enabled: true,
      provisioning_source: 'local',
      authentication: undefined as
        | undefined
        | {
            credential_kind: 'opaque_session'
            session_id: string
            session_auth_method: 'local' | 'oidc'
            mfa_method: 'totp' | 'external' | null
            recently_authenticated: boolean
            recent_authentication_expires_at: string | null
            identity_provider_mfa_asserted: boolean
            reauthentication_endpoint: string
            security_actions_supported: boolean
          },
      features: {
        ai_enabled: false,
        ai_configured: false,
        ai_summary_enabled: false,
        ai_relevance_enabled: false,
        ai_daily_brief_enabled: false,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  },
  usersData: [
    {
      id: 'user-1',
      email: 'analyst@example.com',
      role: 'analyst',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
      password_login_enabled: true,
      provisioning_source: 'local',
      authentication_methods: ['password'],
      oidc_provider_name: null,
      oidc_linked_at: null,
      oidc_last_login_at: null,
      password_managed_by: 'local',
      role_managed_by: 'local',
      mfa_enabled: false,
      mfa_confirmed_at: null,
      active_session_count: 0,
      security_version: 4,
    },
  ] as AdminUser[],
}))

const routerMocks = vi.hoisted(() => {
  const blocker = {
    state: 'unblocked' as 'unblocked' | 'blocked',
    proceed: vi.fn(),
    reset: vi.fn(),
  }

  return {
    blocker,
    useBlocker: vi.fn(() => ({ ...blocker })),
  }
})

vi.mock('../api/client', () => ({ apiFetch: usersPageDomMocks.apiFetch }))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => usersPageDomMocks.queryClient,
  useQuery: (options: { queryKey?: unknown[] }) => {
    if (options.queryKey?.join(':') === 'auth:security:mfa') {
      return {
        data: usersPageDomMocks.ownMfaData,
        isLoading: false,
        isError: Boolean(usersPageDomMocks.ownMfaError),
        isSuccess: !usersPageDomMocks.ownMfaError,
        isFetching: false,
        error: usersPageDomMocks.ownMfaError,
        refetch: usersPageDomMocks.queryRefetch,
      }
    }
    if (options.queryKey?.join(':') === 'auth:security:sessions') {
      return {
        data: usersPageDomMocks.currentSessionsData,
        isLoading: false,
        isError: false,
        isSuccess: true,
        isFetching: false,
        error: null,
        refetch: usersPageDomMocks.queryRefetch,
      }
    }
    const status = usersPageDomMocks.directoryStatus
    return {
      data:
        status === 'success'
          ? {
              users: usersPageDomMocks.usersData,
              total:
                usersPageDomMocks.directoryTotal ??
                usersPageDomMocks.usersData.length,
              limit: usersPageDomMocks.directoryLimit,
              offset: usersPageDomMocks.directoryOffset,
              has_more: usersPageDomMocks.directoryHasMore,
            }
          : undefined,
      isLoading: status === 'loading',
      isError: status === 'error',
      isSuccess: status === 'success',
      isFetching: status === 'loading',
      error: status === 'error' ? usersPageDomMocks.directoryError : null,
      refetch: usersPageDomMocks.queryRefetch,
    }
  },
  useMutation: (options: {
    mutationKey?: unknown
    gcTime?: number
    onMutate?: (variables: never) => void
    onSuccess?: (data: never, variables: never) => void
    onError?: (error: unknown, variables: never) => void
    onSettled?: (data: never, error: unknown, variables: never) => void
  }) => {
    const mutationKey = Array.isArray(options.mutationKey)
      ? options.mutationKey.join(':')
      : ''
    usersPageDomMocks.mutationGcTimes[mutationKey] = options.gcTime
    const reset = vi.fn()
    const createRecord = (
      variables: unknown,
    ): (typeof usersPageDomMocks.mutationRecords)[number] => {
      const record = {
        mutationKey: Array.isArray(options.mutationKey)
          ? options.mutationKey
          : [],
        state: {
          status: 'pending' as const,
          variables,
          error: null as unknown,
        },
      }
      usersPageDomMocks.mutationRecords.push(record)
      return record
    }
    const finish = (
      record: ReturnType<typeof createRecord>,
      variables: unknown,
      data: unknown,
    ) => {
      const error = usersPageDomMocks.nextMutationErrors[mutationKey]
      delete usersPageDomMocks.nextMutationErrors[mutationKey]
      if (error) {
        record.state = { ...record.state, status: 'error', error }
        options.onError?.(error, variables as never)
        options.onSettled?.(undefined as never, error, variables as never)
        return
      }
      record.state = { ...record.state, status: 'success', error: null }
      options.onSuccess?.(data as never, variables as never)
      options.onSettled?.(data as never, null, variables as never)
    }
    if (mutationKey === 'users:create') {
      return {
        mutate: (payload: unknown) => {
          const record = createRecord(payload)
          options.onMutate?.(payload as never)
          usersPageDomMocks.mutate(payload)
          finish(record, payload, {})
        },
        reset,
        isPending: false,
        isError: false,
        error: null,
        variables: null,
      }
    }
    if (mutationKey === 'users:update') {
      return {
        mutate: (payload: { id: string; body: unknown }) => {
          const record = createRecord(payload)
          options.onMutate?.(payload as never)
          usersPageDomMocks.mutate(payload)
          usersPageDomMocks.pendingUserUpdates[payload.id] = (
            error?: unknown,
          ) => {
            if (error) usersPageDomMocks.nextMutationErrors[mutationKey] = error
            finish(record, payload, {})
          }
        },
        reset,
        isPending: false,
        isError: false,
        error: null,
        variables: null,
      }
    }
    if (mutationKey === 'users:mfa-reset') {
      return {
        mutate: (
          payload: NonNullable<typeof usersPageDomMocks.mfaResetPayload>,
        ) => {
          const record = createRecord(payload)
          options.onMutate?.(payload as never)
          usersPageDomMocks.mfaResetPayload = payload
          finish(record, payload, {
            status: 'ok',
            disabled: true,
            revoked_api_tokens: 1,
            revoked_auth_sessions: 2,
          })
        },
        reset,
        isPending: false,
        isError: false,
        error: null,
        variables: null,
      }
    }

    return {
      mutate: usersPageDomMocks.mutate,
      reset,
      isPending: false,
      isError: false,
      error: null,
      variables: null,
    }
  },
}))

vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
    useNavigate: () => usersPageDomMocks.navigate,
    useLocation: () => ({
      pathname: '/settings/users',
      search: '',
      hash: '',
      state: usersPageDomMocks.locationState,
      key: 'test',
    }),
  }
})

vi.mock('../components/AuthContext', () => ({
  useAuth: () => ({ markLoggedOut: usersPageDomMocks.markLoggedOut }),
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => usersPageDomMocks.currentUser,
}))

import { UsersPage } from './UsersPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  if (!usersPageDomMocks.apiFetch.getMockImplementation()) {
    usersPageDomMocks.apiFetch.mockImplementation((path: string) => {
      if (path.startsWith('/users/')) {
        if (usersPageDomMocks.userLookupError) {
          return Promise.reject(usersPageDomMocks.userLookupError)
        }
        const userId = decodeURIComponent(path.slice('/users/'.length))
        const target =
          usersPageDomMocks.userLookupResult ??
          usersPageDomMocks.usersData.find((user) => user.id === userId)
        return target
          ? Promise.resolve(target)
          : Promise.reject(new Error('The user account was not found.'))
      }
      return Promise.resolve({})
    })
  }
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<UsersPage />)
  })
  return container
}

function pageText() {
  return document.body.textContent ?? ''
}

function credentialMutationVariables(mutationKey: readonly unknown[]) {
  return usersPageDomMocks.mutationCache
    .findAll({ mutationKey })
    .map((record) => record.state.variables)
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

function rerenderPage() {
  act(() => {
    root?.render(<UsersPage />)
  })
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setTextAreaValue(input: HTMLTextAreaElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    'value',
  )
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  usersPageDomMocks.usersData = [
    {
      id: 'user-1',
      email: 'analyst@example.com',
      role: 'analyst',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
      password_login_enabled: true,
      provisioning_source: 'local',
      authentication_methods: ['password'],
      oidc_provider_name: null,
      oidc_linked_at: null,
      oidc_last_login_at: null,
      password_managed_by: 'local',
      role_managed_by: 'local',
      mfa_enabled: false,
      mfa_confirmed_at: null,
      active_session_count: 0,
      security_version: 4,
    },
  ]
  usersPageDomMocks.apiFetch.mockReset()
  usersPageDomMocks.userLookupResult = null
  usersPageDomMocks.userLookupError = null
  usersPageDomMocks.mfaResetPayload = null
  usersPageDomMocks.locationState = null
  usersPageDomMocks.directoryStatus = 'success'
  usersPageDomMocks.directoryError = null
  usersPageDomMocks.directoryTotal = null
  usersPageDomMocks.directoryOffset = 0
  usersPageDomMocks.directoryLimit = 100
  usersPageDomMocks.directoryHasMore = false
  usersPageDomMocks.ownMfaData = {
    local_mfa_available: true,
    managed_by: 'local',
    enabled: false,
    confirmed_at: null,
    recovery_codes_remaining: 0,
  }
  usersPageDomMocks.ownMfaError = null
  usersPageDomMocks.currentSessionsData = {
    sessions: [
      {
        id: 'session-admin-1',
        current: true,
        auth_method: 'local',
        mfa_method: 'totp',
        client_ip: '192.0.2.10',
        user_agent: 'Test browser',
        authenticated_at: '2026-08-27T10:00:00Z',
        last_seen_at: '2026-08-27T10:00:00Z',
        idle_expires_at: '2026-08-28T10:00:00Z',
        absolute_expires_at: '2026-09-03T10:00:00Z',
        revoked_at: null,
        revoked_reason: null,
      },
    ],
    active_count: 1,
    history_truncated: false,
  }
  usersPageDomMocks.currentUser = {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
      password_login_enabled: true,
      provisioning_source: 'local',
      authentication: undefined,
      features: {
        ai_enabled: false,
        ai_configured: false,
        ai_summary_enabled: false,
        ai_relevance_enabled: false,
        ai_daily_brief_enabled: false,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  }
  usersPageDomMocks.mutate.mockReset()
  usersPageDomMocks.navigate.mockReset()
  usersPageDomMocks.markLoggedOut.mockReset()
  usersPageDomMocks.pendingUserUpdates = {}
  usersPageDomMocks.mutationRecords.splice(0)
  usersPageDomMocks.queryRefetch.mockReset()
  usersPageDomMocks.mutationGcTimes = {}
  usersPageDomMocks.nextMutationErrors = {}
  routerMocks.blocker.state = 'unblocked'
  routerMocks.blocker.proceed.mockReset()
  routerMocks.blocker.reset.mockReset()
})

describe('UsersPage DOM workflows', () => {
  it('progressively discloses mobile create and user management controls', () => {
    const view = renderPage()
    const createForm = view.querySelector<HTMLElement>('#create-user-form')
    const createToggle = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'New local user',
    )
    const userSettings = view.querySelector<HTMLElement>(
      '#user-settings-user-1',
    )
    const userManagement = view.querySelector<HTMLElement>(
      '#user-management-user-1',
    )
    const manageToggle = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Manage',
    )

    expect(createForm?.className).toContain('hidden')
    expect(createToggle?.getAttribute('aria-expanded')).toBe('false')
    expect(userManagement?.className).toContain('hidden')

    act(() => {
      createToggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      manageToggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(createForm?.className).toContain('grid')
    expect(createToggle?.getAttribute('aria-expanded')).toBe('true')
    expect(userSettings?.className).toContain('grid')
    expect(userManagement?.className).toContain('block')
    expect(manageToggle?.getAttribute('aria-expanded')).toBe('true')
  })

  it('labels and restores a create-user draft after the form is closed', () => {
    const view = renderPage()
    const createToggle = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.trim() === 'New local user',
    )!

    act(() => createToggle.click())
    act(() =>
      setInputValue(
        view.querySelector<HTMLInputElement>('#create-user-email')!,
        'draft-user@example.com',
      ),
    )
    act(() => createToggle.click())

    expect(createToggle.textContent?.trim()).toBe('Resume local user draft')
    expect(createToggle.getAttribute('aria-expanded')).toBe('false')
    act(() => createToggle.click())
    expect(view.querySelector<HTMLInputElement>('#create-user-email')?.value).toBe(
      'draft-user@example.com',
    )
  })

  it('shows SSO ownership and omits locally managed credential and role controls', () => {
    usersPageDomMocks.usersData = [
      {
        id: 'user-1',
        email: 'sso-user@example.com',
        role: 'viewer',
        is_active: true,
        is_approved: true,
        approved_at: '2026-04-20T10:00:00Z',
        created_at: '2026-04-19T10:00:00Z',
        password_login_enabled: false,
        provisioning_source: 'oidc',
        authentication_methods: ['oidc'],
        oidc_provider_name: 'Authentik',
        oidc_linked_at: '2026-04-19T10:00:00Z',
        oidc_last_login_at: '2026-04-21T10:00:00Z',
        password_managed_by: 'oidc',
        role_managed_by: 'oidc',
        mfa_enabled: false,
        mfa_confirmed_at: null,
        active_session_count: 1,
      },
    ]

    const view = renderPage()

    expect(view.textContent).toContain('SSO-provisioned')
    expect(view.textContent).toContain('Sign-in: SSO available')
    expect(view.textContent).toContain('Provider: Authentik')
    expect(view.textContent).toContain('Managed by Authentik')
    expect(view.textContent).toContain('Local MFA not applicable')
    expect(view.textContent).not.toContain('MFA managed by SSO')
    expect(view.textContent).toContain('1 tracked browser session')
    expect(view.textContent).toContain('legacy JWT sessions are not included')
    expect(view.textContent).toContain('Credentials are managed by Authentik')
    expect(view.querySelector('#user-role-user-1')).toBeNull()
    expect(view.querySelector('#user-reset-password-user-1')).toBeNull()
  })

  it('requires administrator reauthentication and a reason before resetting local MFA', async () => {
    usersPageDomMocks.usersData[0] = {
      ...usersPageDomMocks.usersData[0],
      mfa_enabled: true,
      mfa_confirmed_at: '2026-04-21T10:00:00Z',
      active_session_count: 2,
    }
    usersPageDomMocks.ownMfaData = {
      ...usersPageDomMocks.ownMfaData,
      enabled: true,
      confirmed_at: '2026-04-21T10:00:00Z',
      recovery_codes_remaining: 8,
    }
    const view = renderPage()

    act(() => {
      Array.from(view.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Manage')
        ?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(view.textContent).toContain('Local MFA enabled')
    expect(view.textContent).toContain('2 tracked browser sessions')
    act(() => {
      Array.from(view.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Reset MFA')
        ?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const dialog = document.querySelector('[role="alertdialog"]')
    const confirm = Array.from(
      dialog?.querySelectorAll<HTMLButtonElement>('button') ?? [],
    ).find(
      (button) => button.textContent?.trim() === 'Reset MFA and revoke access',
    )
    expect(confirm?.disabled).toBe(true)
    act(() => {
      setTextAreaValue(
        document.querySelector<HTMLTextAreaElement>('#admin-mfa-reset-reason')!,
        'Lost authenticator',
      )
      setInputValue(
        document.querySelector<HTMLInputElement>('#admin-mfa-reset-password')!,
        'AdminPass123!',
      )
      setInputValue(
        document.querySelector<HTMLInputElement>('#admin-mfa-reset-code')!,
        '123456',
      )
    })
    expect(confirm?.disabled).toBe(false)
    act(() =>
      confirm?.dispatchEvent(new MouseEvent('click', { bubbles: true })),
    )

    expect(usersPageDomMocks.mfaResetPayload).toMatchObject({
      target: { id: 'user-1' },
      draft: {
        reason: 'Lost authenticator',
        currentPassword: 'AdminPass123!',
        code: '123456',
      },
    })
    expect(pageText()).toContain('MFA reset for analyst@example.com')
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['users', 'mfa-reset'])).toHaveLength(
        0,
      )
    })
  })

  it('uses the current OIDC session for MFA recovery and resumes after provider verification', async () => {
    usersPageDomMocks.usersData[0] = {
      ...usersPageDomMocks.usersData[0],
      mfa_enabled: true,
      mfa_confirmed_at: '2026-04-21T10:00:00Z',
    }
    usersPageDomMocks.currentUser.data.authentication = {
      credential_kind: 'opaque_session',
      session_id: 'session-admin-1',
      session_auth_method: 'oidc',
      mfa_method: 'external',
      recently_authenticated: true,
      recent_authentication_expires_at: '2026-08-27T10:10:00Z',
      identity_provider_mfa_asserted: true,
      reauthentication_endpoint: '/auth/oidc/reauth',
      security_actions_supported: true,
    }
    usersPageDomMocks.ownMfaError = new Error(
      'Local MFA status is unavailable and irrelevant to this SSO session',
    )
    usersPageDomMocks.locationState = {
      oidcReauth: {
        result: 'success',
        purpose: 'admin_mfa_reset',
        context: {
          targetUserId: 'user-1',
          targetEmail: 'analyst@example.com',
          reason: 'Lost SSO-bound device',
        },
      },
    }

    renderPage()
    await act(async () => await flushPromises())
    await vi.waitFor(() =>
      expect(document.querySelector('#admin-mfa-reset-reason')).not.toBeNull(),
    )

    expect(document.querySelector('#admin-mfa-reset-password')).toBeNull()
    expect(document.body.textContent).toContain(
      'Identity-provider verification completed',
    )
    expect(
      document.querySelector<HTMLTextAreaElement>('#admin-mfa-reset-reason')
        ?.value,
    ).toBe('Lost SSO-bound device')
    const confirm = [
      ...document.querySelectorAll<HTMLButtonElement>('button'),
    ].find((button) => button.textContent === 'Reset MFA and revoke access')
    expect(confirm?.disabled).toBe(false)
    await act(async () => {
      confirm?.click()
      await flushPromises()
    })

    expect(usersPageDomMocks.mfaResetPayload).toMatchObject({
      target: { id: 'user-1' },
      draft: {
        reason: 'Lost SSO-bound device',
        currentPassword: '',
        code: '',
      },
    })
    expect(usersPageDomMocks.navigate).toHaveBeenCalledWith('/settings/users', {
      replace: true,
      state: null,
    })
    expect(document.querySelector('#admin-mfa-reset-reason')).toBeNull()
  })

  it('restores an off-page MFA-reset target by ID after SSO verification', async () => {
    const offPageTarget: AdminUser = {
      ...usersPageDomMocks.usersData[0],
      id: 'user-205',
      email: 'off-page@example.com',
      mfa_enabled: true,
      mfa_confirmed_at: '2026-04-21T10:00:00Z',
    }
    usersPageDomMocks.userLookupResult = offPageTarget
    usersPageDomMocks.locationState = {
      oidcReauth: {
        result: 'success',
        purpose: 'admin_mfa_reset',
        context: {
          targetUserId: offPageTarget.id,
          targetEmail: offPageTarget.email,
          reason: 'Lost off-page authenticator',
        },
      },
    }

    renderPage()
    await act(async () => await flushPromises())
    await vi.waitFor(() =>
      expect(document.querySelector('#admin-mfa-reset-reason')).not.toBeNull(),
    )

    expect(usersPageDomMocks.apiFetch).toHaveBeenCalledWith('/users/user-205')
    expect(document.body.textContent).toContain('off-page@example.com')
    expect(
      document.querySelector<HTMLTextAreaElement>('#admin-mfa-reset-reason')
        ?.value,
    ).toBe('Lost off-page authenticator')
  })

  it('announces an MFA target restoration failure as an error', async () => {
    usersPageDomMocks.userLookupError = new Error('Directory lookup failed.')
    usersPageDomMocks.locationState = {
      oidcReauth: {
        result: 'success',
        purpose: 'admin_mfa_reset',
        context: {
          targetUserId: 'missing-user',
          targetEmail: 'missing@example.com',
          reason: 'Lost authenticator',
        },
      },
    }

    renderPage()
    await act(async () => await flushPromises())
    const notice = await vi.waitFor(() => {
      const candidate = document.querySelector<HTMLElement>('[role="alert"]')
      expect(candidate).not.toBeNull()
      return candidate!
    })

    expect(notice.getAttribute('aria-live')).toBe('assertive')
    expect(notice.className).toContain('text-red-700')
    expect(notice.className).not.toContain('text-green-800')
    expect(notice.textContent).toContain(
      'The MFA-reset target for missing@example.com could not be restored',
    )
  })

  it('starts OIDC step-up from the current session even for a locally provisioned admin', () => {
    usersPageDomMocks.usersData[0] = {
      ...usersPageDomMocks.usersData[0],
      mfa_enabled: true,
    }
    usersPageDomMocks.currentUser.data.authentication = {
      credential_kind: 'opaque_session',
      session_id: 'session-admin-1',
      session_auth_method: 'oidc',
      mfa_method: 'external',
      recently_authenticated: false,
      recent_authentication_expires_at: null,
      identity_provider_mfa_asserted: true,
      reauthentication_endpoint: '/auth/oidc/reauth',
      security_actions_supported: true,
    }
    const view = renderPage()
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Manage')
        ?.click()
    })
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Reset MFA')
        ?.click()
    })
    act(() => {
      setTextAreaValue(
        document.querySelector<HTMLTextAreaElement>('#admin-mfa-reset-reason')!,
        'Lost authenticator',
      )
    })
    const verify = [
      ...document.querySelectorAll<HTMLButtonElement>('button'),
    ].find((button) => button.textContent === 'Verify with SSO')
    expect(document.querySelector('#admin-mfa-reset-password')).toBeNull()
    expect(verify?.disabled).toBe(false)
    act(() => verify?.click())
    expect(usersPageDomMocks.mutate).toHaveBeenCalledWith({
      target: expect.objectContaining({ id: 'user-1' }),
      reason: 'Lost authenticator',
    })
  })

  it('reviews the create-user request before posting it', async () => {
    const view = renderPage()
    const createSection = view.querySelector('section')
    const createForm = createSection?.querySelector('form')

    const emailInput =
      createSection?.querySelector<HTMLInputElement>('#create-user-email')
    const passwordInput = createSection?.querySelector<HTMLInputElement>(
      '#create-user-password',
    )
    const roleSelect =
      createSection?.querySelector<HTMLSelectElement>('#create-user-role')

    expect(emailInput).not.toBeNull()
    expect(passwordInput).not.toBeNull()
    expect(roleSelect).not.toBeNull()
    expect(createForm).not.toBeNull()

    act(() => {
      setInputValue(emailInput!, ' admin@example.com ')
      setInputValue(passwordInput!, 'temporary-password')
      setSelectValue(roleSelect!, 'admin')
    })

    act(() => {
      createForm!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
    })

    expect(pageText()).toContain('Create local user account?')
    expect(pageText()).toContain('admin@example.com')
    expect(pageText()).toContain(
      'This account will have full administrative access on first sign-in.',
    )
    expect(pageText()).toContain(
      'The account will skip the pending-approval state.',
    )

    const confirmButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Create local user',
    )
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(usersPageDomMocks.mutate).toHaveBeenCalledWith({
      email: 'admin@example.com',
      password: 'temporary-password',
      role: 'admin',
      is_active: true,
      is_approved: true,
    })
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['users', 'create'])).toHaveLength(0)
    })
    expect(passwordInput?.value).toBe('')
  })

  it('uses zero-retention mutation caches for every administrator credential workflow', () => {
    renderPage()

    expect(usersPageDomMocks.mutationGcTimes).toMatchObject({
      'users:create': 0,
      'users:update': 0,
      'users:mfa-reset': 0,
    })
  })

  it('keeps a failed password-reset field retryable while purging mutation variables immediately', async () => {
    const view = renderPage()
    act(() => {
      Array.from(view.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Manage')
        ?.click()
    })
    const passwordInput = view.querySelector<HTMLInputElement>(
      '#user-reset-password-user-1',
    )!
    act(() => setInputValue(passwordInput, 'RetryPassword123!'))
    act(() => {
      Array.from(view.querySelectorAll('button'))
        .find(
          (button) => button.textContent?.trim() === 'Review password reset',
        )
        ?.click()
    })
    act(() => {
      Array.from(document.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Reset password')
        ?.click()
    })
    act(() => {
      usersPageDomMocks.pendingUserUpdates['user-1']?.(
        new Error('temporary failure'),
      )
    })

    expect(passwordInput.value).toBe('RetryPassword123!')
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['users', 'update'])).toHaveLength(0)
    })

    act(() => setInputValue(passwordInput, ''))
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['users', 'update'])).toHaveLength(0)
    })
    expect(passwordInput.value).toBe('')

    act(() => setInputValue(passwordInput, 'ReplacementPassword123!'))
    act(() => {
      Array.from(view.querySelectorAll('button'))
        .find(
          (button) => button.textContent?.trim() === 'Review password reset',
        )
        ?.click()
    })
    act(() => {
      Array.from(document.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Reset password')
        ?.click()
    })
    act(() => {
      usersPageDomMocks.pendingUserUpdates['user-1']?.()
    })
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['users', 'update'])).toHaveLength(0)
    })
    expect(passwordInput.value).toBe('')
  })

  it('keeps failed MFA step-up fields retryable while purging mutation variables immediately', async () => {
    usersPageDomMocks.usersData[0] = {
      ...usersPageDomMocks.usersData[0],
      mfa_enabled: true,
      mfa_confirmed_at: '2026-04-21T10:00:00Z',
    }
    usersPageDomMocks.ownMfaData = {
      ...usersPageDomMocks.ownMfaData,
      enabled: true,
    }
    usersPageDomMocks.nextMutationErrors['users:mfa-reset'] = new Error(
      'temporary failure',
    )
    const view = renderPage()
    act(() => {
      Array.from(view.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Manage')
        ?.click()
      Array.from(view.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Reset MFA')
        ?.click()
    })
    act(() => {
      setTextAreaValue(
        document.querySelector<HTMLTextAreaElement>('#admin-mfa-reset-reason')!,
        'Lost device',
      )
      setInputValue(
        document.querySelector<HTMLInputElement>('#admin-mfa-reset-password')!,
        'AdminRetryPass123!',
      )
      setInputValue(
        document.querySelector<HTMLInputElement>('#admin-mfa-reset-code')!,
        '654321',
      )
    })
    act(() => {
      Array.from(document.querySelectorAll('button'))
        .find(
          (button) =>
            button.textContent?.trim() === 'Reset MFA and revoke access',
        )
        ?.click()
    })

    expect(
      document.querySelector<HTMLInputElement>('#admin-mfa-reset-password')
        ?.value,
    ).toBe('AdminRetryPass123!')
    await vi.waitFor(() => {
      expect(credentialMutationVariables(['users', 'mfa-reset'])).toHaveLength(
        0,
      )
    })

    act(() => {
      Array.from(document.querySelectorAll('button'))
        .find((button) => button.textContent?.trim() === 'Cancel')
        ?.click()
    })
    await act(async () => await flushPromises())
    expect(credentialMutationVariables(['users', 'mfa-reset'])).toHaveLength(0)
    expect(document.querySelector('#admin-mfa-reset-password')).toBeNull()
  })

  it('keeps directory loading and error states exclusive and withholds creation until inventory is known', () => {
    usersPageDomMocks.directoryStatus = 'loading'
    let view = renderPage()

    expect(pageText()).toContain('Loading account inventory')
    expect(pageText()).toContain('Loading users')
    expect(view.querySelector('#create-user-form')).toBeNull()
    expect(
      [...view.querySelectorAll('button')].some(
        (button) => button.textContent === 'New local user',
      ),
    ).toBe(false)
    expect(pageText()).not.toContain('No users match')

    act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    document.body.innerHTML = ''
    usersPageDomMocks.directoryStatus = 'error'
    usersPageDomMocks.directoryError = new Error(
      'directory service unavailable',
    )
    view = renderPage()

    expect(pageText()).toContain('Account inventory unavailable')
    expect(pageText()).toContain('directory service unavailable')
    expect(view.querySelector('#create-user-form')).toBeNull()
    expect(pageText()).not.toContain('Loading users')
    expect(pageText()).not.toContain('No users match')
    const retry = [...view.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === 'Retry user directory',
    )
    act(() => retry?.click())
    expect(usersPageDomMocks.queryRefetch).toHaveBeenCalled()
  })

  it('distinguishes a linked but unavailable identity from an unlinked local account', () => {
    usersPageDomMocks.usersData = [
      {
        ...usersPageDomMocks.usersData[0],
        identity_linked: true,
        sso_sign_in_available: false,
        oidc_identity_status: 'linked_unavailable',
        credential_management_source: 'local',
        authentication_methods: ['password', 'oidc'],
        oidc_provider_name: 'Authentik',
        oidc_linked_at: '2026-04-19T10:00:00Z',
      },
    ]

    const view = renderPage()

    expect(view.textContent).toContain('Local + SSO')
    expect(view.textContent).toContain(
      'Sign-in: Password + SSO linked, currently unavailable',
    )
    expect(view.textContent).toContain('SSO unavailable')
    expect(view.querySelector('#user-reset-password-user-1')).not.toBeNull()
  })

  it('uses the authoritative credential source before exposing password controls', () => {
    usersPageDomMocks.usersData = [
      {
        ...usersPageDomMocks.usersData[0],
        credential_management_source: 'oidc',
        password_managed_by: 'local',
        identity_linked: true,
        sso_sign_in_available: true,
        oidc_identity_status: 'linked_available',
        authentication_methods: ['oidc'],
        oidc_provider_name: 'Authentik',
      },
    ]

    const view = renderPage()
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Manage')
        ?.click()
    })

    expect(view.textContent).toContain('Credentials are managed by Authentik')
    expect(view.querySelector('#user-reset-password-user-1')).toBeNull()
  })

  it('renders accessible admin controls and confirms a role change through the review dialog', () => {
    const view = renderPage()

    expect(
      view.querySelector('label[for="create-user-email"]')?.textContent,
    ).toContain('Email')
    expect(
      view
        .querySelector('label[for="create-user-password"]')
        ?.textContent?.toLowerCase(),
    ).toContain('password')
    expect(
      view.querySelector('label[for="create-user-role"]')?.textContent,
    ).toContain('Role')
    expect(
      view.querySelector('label[for="user-directory-search"]')?.textContent,
    ).toContain('email, role, status, account type, or provider')
    expect(
      view.querySelector<HTMLInputElement>('#user-directory-search')
        ?.placeholder,
    ).toBe('Search users...')
    expect(
      view.querySelector('label[for="user-role-user-1"]')?.textContent,
    ).toContain('Role for analyst@example.com')
    expect(
      view.querySelector('label[for="user-reset-password-user-1"]')
        ?.textContent,
    ).toContain('New password for analyst@example.com')

    const roleSelect =
      view.querySelector<HTMLSelectElement>('#user-role-user-1')
    const reviewButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Review changes'),
    )

    expect(roleSelect).not.toBeNull()
    expect(reviewButton).not.toBeNull()

    act(() => {
      roleSelect!.value = 'admin'
      roleSelect!.dispatchEvent(new Event('change', { bubbles: true }))
    })

    act(() => {
      reviewButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Apply privileged user changes?')
    expect(pageText()).toContain('Role will change from analyst to admin.')

    const confirmButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Apply user changes'),
    )
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(usersPageDomMocks.mutate).toHaveBeenCalledWith({
      id: 'user-1',
      body: { role: 'admin', expected_security_version: 4 },
    })
  })

  it('refreshes and explains a coded optimistic-concurrency conflict', () => {
    const conflict = Object.assign(new Error('User changed'), {
      name: 'ApiError',
      code: 'user_security_version_conflict',
    })
    usersPageDomMocks.nextMutationErrors['users:update'] = conflict
    const view = renderPage()
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Manage')
        ?.click()
      setSelectValue(
        view.querySelector<HTMLSelectElement>('#user-role-user-1')!,
        'admin',
      )
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Review changes')
        ?.click()
    })
    act(() => {
      ;[...document.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Apply user changes')
        ?.click()
      usersPageDomMocks.pendingUserUpdates['user-1']?.()
    })

    expect(usersPageDomMocks.queryRefetch).toHaveBeenCalled()
    expect(view.textContent).toContain(
      'This account changed while your draft was open',
    )
    expect(usersPageDomMocks.mutate).toHaveBeenCalledWith({
      id: 'user-1',
      body: { role: 'admin', expected_security_version: 4 },
    })
  })

  it('blocks overlapping two-admin row edits until refresh or reapply is explicit', async () => {
    const view = renderPage()
    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Manage')
        ?.click()
      setSelectValue(
        view.querySelector<HTMLSelectElement>('#user-role-user-1')!,
        'admin',
      )
    })

    usersPageDomMocks.usersData = [
      {
        ...usersPageDomMocks.usersData[0],
        role: 'viewer',
        is_active: false,
      },
    ]
    rerenderPage()
    await act(async () => await flushPromises())

    expect(view.textContent).toContain('This account changed on the server')
    expect(view.textContent).toContain(
      'server has viewer; your draft has admin',
    )
    expect(
      view.querySelector<HTMLInputElement>(
        'input[aria-label="Active account for analyst@example.com"]',
      )?.checked,
    ).toBe(false)
    const review = [...view.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === 'Review changes',
    )
    expect(review?.disabled).toBe(true)

    act(() => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Reapply my changes')
        ?.click()
    })
    expect(
      view.querySelector<HTMLSelectElement>('#user-role-user-1')?.value,
    ).toBe('admin')
    expect(view.textContent).not.toContain('This account changed on the server')
    expect(review?.disabled).toBe(false)
  })

  it('keeps pending user updates scoped to the affected row', () => {
    usersPageDomMocks.usersData = [
      usersPageDomMocks.usersData[0],
      {
        ...usersPageDomMocks.usersData[0],
        id: 'user-2',
        email: 'second-analyst@example.com',
      },
    ]
    const view = renderPage()
    const firstSettings = view.querySelector<HTMLElement>(
      '#user-settings-user-1',
    )
    const secondSettings = view.querySelector<HTMLElement>(
      '#user-settings-user-2',
    )
    const firstRole =
      firstSettings?.querySelector<HTMLSelectElement>('#user-role-user-1')
    const secondRole =
      secondSettings?.querySelector<HTMLSelectElement>('#user-role-user-2')
    const firstReview =
      firstSettings?.querySelector<HTMLButtonElement>('button')
    const secondReview =
      secondSettings?.querySelector<HTMLButtonElement>('button')

    act(() => {
      setSelectValue(firstRole!, 'admin')
      setSelectValue(secondRole!, 'admin')
    })
    act(() => {
      firstReview?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const confirmButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Apply user changes'),
    )
    act(() => {
      confirmButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(firstReview?.disabled).toBe(true)
    expect(secondReview?.disabled).toBe(false)

    act(() => {
      usersPageDomMocks.pendingUserUpdates['user-1']?.()
    })
    expect(firstReview?.disabled).toBe(false)
  })

  it('warns admins before they lock themselves out by changing their own role, active state, or approval', () => {
    usersPageDomMocks.currentUser = {
      data: {
        id: 'admin-1',
        email: 'admin@example.com',
        role: 'admin',
        is_active: true,
        is_approved: true,
        approved_at: '2026-04-20T10:00:00Z',
        created_at: '2026-04-19T10:00:00Z',
        password_login_enabled: true,
        provisioning_source: 'local',
        authentication: undefined,
        features: {
          ai_enabled: false,
          ai_configured: false,
          ai_summary_enabled: false,
          ai_relevance_enabled: false,
          ai_daily_brief_enabled: false,
        },
      },
      isLoading: false,
      isError: false,
      error: null,
    }
    usersPageDomMocks.usersData = [
      {
        id: 'admin-1',
        email: 'admin@example.com',
        role: 'admin',
        is_active: true,
        is_approved: true,
        approved_at: '2026-04-20T10:00:00Z',
        created_at: '2026-04-19T10:00:00Z',
        password_login_enabled: true,
        provisioning_source: 'local',
        authentication_methods: ['password'],
        oidc_provider_name: null,
        oidc_linked_at: null,
        oidc_last_login_at: null,
        password_managed_by: 'local',
        role_managed_by: 'local',
        mfa_enabled: false,
        mfa_confirmed_at: null,
        active_session_count: 1,
        security_version: 8,
      },
    ]

    const view = renderPage()
    const roleSelect =
      view.querySelector<HTMLSelectElement>('#user-role-admin-1')
    const controls = view.querySelector('#user-settings-admin-1')
    const rowCheckboxes = Array.from(
      controls?.querySelectorAll<HTMLInputElement>('input[type="checkbox"]') ??
        [],
    )
    const activeCheckbox = rowCheckboxes[0]
    const approvedCheckbox = rowCheckboxes[1]
    const reviewButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Review changes'),
    )

    expect(roleSelect).not.toBeNull()
    expect(activeCheckbox).not.toBeNull()
    expect(approvedCheckbox).not.toBeNull()
    expect(reviewButton).not.toBeNull()

    act(() => {
      setSelectValue(roleSelect!, 'viewer')
      activeCheckbox!.click()
      approvedCheckbox!.click()
    })

    expect(pageText()).toContain('Self-access warning')
    expect(pageText()).toContain('You are removing your own admin access.')
    expect(pageText()).toContain('You are disabling your own account.')
    expect(pageText()).toContain(
      'You are sending your own account back to pending approval.',
    )

    act(() => {
      reviewButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Apply self-access changes?')
    expect(pageText()).toContain('Lockout risk')

    const confirmButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Apply self-access changes'),
    )
    expect(confirmButton).not.toBeNull()

    act(() => {
      confirmButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(usersPageDomMocks.mutate).toHaveBeenCalledWith({
      id: 'admin-1',
      body: {
        role: 'viewer',
        is_active: false,
        is_approved: false,
        expected_security_version: 8,
      },
    })
  })

  it('keeps unsaved row settings while a server-side email search refresh is pending', () => {
    const view = renderPage()

    const roleSelect =
      view.querySelector<HTMLSelectElement>('#user-role-user-1')
    const searchInput = view.querySelector<HTMLInputElement>(
      '#user-directory-search',
    )

    expect(roleSelect).not.toBeNull()
    expect(searchInput).not.toBeNull()

    act(() => {
      setSelectValue(roleSelect!, 'admin')
      setInputValue(searchInput!, 'missing-user')
    })

    expect(
      view.querySelector<HTMLSelectElement>('#user-role-user-1')?.value,
    ).toBe('admin')

    act(() => {
      setInputValue(searchInput!, '')
    })

    expect(
      view.querySelector<HTMLSelectElement>('#user-role-user-1')?.value,
    ).toBe('admin')
  })

  it('exposes and restores an account draft hidden by server-side filtering', () => {
    const hiddenUser = usersPageDomMocks.usersData[0]
    const view = renderPage()
    const roleSelect = view.querySelector<HTMLSelectElement>('#user-role-user-1')!
    const searchInput = view.querySelector<HTMLInputElement>('#user-directory-search')!

    act(() => setSelectValue(roleSelect, 'admin'))
    usersPageDomMocks.usersData = []
    act(() => setInputValue(searchInput, 'missing-user'))

    expect(pageText()).toContain(
      '1 unsaved account draft is hidden by the current filters or directory page.',
    )
    const showDraft = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Show draft for analyst@example.com',
    )
    expect(showDraft).not.toBeNull()

    usersPageDomMocks.usersData = [hiddenUser]
    act(() => showDraft?.click())

    expect(view.querySelector<HTMLInputElement>('#user-directory-search')?.value).toBe(
      'analyst@example.com',
    )
    expect(view.querySelector<HTMLSelectElement>('#user-role-user-1')?.value).toBe('admin')
    expect(pageText()).not.toContain('unsaved account draft is hidden')
  })

  it('warns before blocked navigation when user settings drafts are still dirty', () => {
    const view = renderPage()

    const roleSelect =
      view.querySelector<HTMLSelectElement>('#user-role-user-1')
    expect(roleSelect).not.toBeNull()

    act(() => {
      setSelectValue(roleSelect!, 'admin')
    })

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('Discard unsaved user changes?')

    const cancelButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.trim() === 'Cancel')
      .at(-1)
    expect(cancelButton).not.toBeNull()

    act(() => {
      cancelButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(routerMocks.blocker.reset).toHaveBeenCalledTimes(1)

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    const discardButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Discard changes'),
    )
    expect(discardButton).not.toBeNull()

    act(() => {
      discardButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(routerMocks.blocker.proceed).toHaveBeenCalledTimes(1)
  })

  it('treats a dirty create-user form as unsaved work before navigation', () => {
    const view = renderPage()

    const emailInput =
      view.querySelector<HTMLInputElement>('#create-user-email')
    expect(emailInput).not.toBeNull()

    act(() => {
      setInputValue(emailInput!, 'new-analyst@example.com')
    })

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('Discard unsaved user changes?')
  })

  it('keeps password-reset drafts while server-side email filtering refreshes', () => {
    const view = renderPage()

    const passwordInput = view.querySelector<HTMLInputElement>(
      '#user-reset-password-user-1',
    )
    const searchInput = view.querySelector<HTMLInputElement>(
      '#user-directory-search',
    )
    expect(passwordInput).not.toBeNull()
    expect(searchInput).not.toBeNull()

    act(() => {
      setInputValue(passwordInput!, 'temporary-password')
      setInputValue(searchInput!, 'missing-user')
    })

    expect(
      view.querySelector<HTMLInputElement>('#user-reset-password-user-1')
        ?.value,
    ).toBe('temporary-password')

    act(() => {
      setInputValue(searchInput!, '')
    })

    expect(
      view.querySelector<HTMLInputElement>('#user-reset-password-user-1')
        ?.value,
    ).toBe('temporary-password')

    routerMocks.blocker.state = 'blocked'
    rerenderPage()

    expect(pageText()).toContain('Discard unsaved user changes?')
  })
})
