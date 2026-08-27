// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const tokensPageDomMocks = vi.hoisted(() => ({
  currentUser: {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-21T10:00:00Z',
      created_at: '2026-04-20T10:00:00Z',
      features: {
        ai_enabled: true,
        ai_configured: true,
        ai_summary_enabled: true,
        ai_relevance_enabled: true,
        ai_daily_brief_enabled: true,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue({}),
  },
  queryClient: {
    invalidateQueries: vi.fn(),
    getMutationCache: () => tokensPageDomMocks.mutationCache,
  },
  mutationRecords: [] as Array<{
    mutationKey: unknown[]
    state: {
      status: 'pending' | 'success' | 'error'
      variables: unknown
      error: unknown
    }
  }>,
  mutationCache: {
    findAll: ({ mutationKey }: { mutationKey: readonly unknown[] }) =>
      tokensPageDomMocks.mutationRecords.filter(
        (record) =>
          JSON.stringify(record.mutationKey) === JSON.stringify(mutationKey),
      ),
    remove: (record: unknown) => {
      const index = tokensPageDomMocks.mutationRecords.indexOf(record as never)
      if (index >= 0) tokensPageDomMocks.mutationRecords.splice(index, 1)
    },
  },
  createMutate: vi.fn(),
  revokeMutate: vi.fn(),
  oidcReauthMutate: vi.fn(),
  mfaEnabled: false,
  mfaError: null as unknown,
  mfaRefetch: vi.fn(),
  tokensError: null as unknown,
  tokensRefetch: vi.fn(),
  tokenQueryKeys: [] as unknown[][],
  nextCreateError: null as unknown,
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function tokenMutationResult(mutate: (variables: unknown) => void) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
  }
}

const tokenInventory = [
  {
    id: 'token-1',
    user_id: 'admin-1',
    name: 'Legacy automation',
    token_prefix: 'tl_legacy',
    scopes: [],
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    created_at: '2026-04-20T10:00:00Z',
  },
  {
    id: 'token-2',
    user_id: 'viewer-2',
    name: 'Partner sync',
    token_prefix: 'tl_partner',
    scopes: ['read:feeds'],
    last_used_at: '2026-04-22T10:00:00Z',
    expires_at: '2026-07-20T10:00:00Z',
    revoked_at: null,
    created_at: '2026-04-21T10:00:00Z',
  },
]

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => tokensPageDomMocks.queryClient,
  useQuery: (options: { queryKey?: unknown[] }) => {
    if (options.queryKey?.join(':') === 'auth:security:mfa') {
      return {
        data: tokensPageDomMocks.mfaError
          ? undefined
          : {
              local_mfa_available: true,
              managed_by: 'local',
              enabled: tokensPageDomMocks.mfaEnabled,
              confirmed_at: tokensPageDomMocks.mfaEnabled
                ? '2026-08-27T10:00:00Z'
                : null,
              recovery_codes_remaining: tokensPageDomMocks.mfaEnabled ? 8 : 0,
            },
        isLoading: false,
        isFetching: false,
        isError: Boolean(tokensPageDomMocks.mfaError),
        error: tokensPageDomMocks.mfaError,
        refetch: tokensPageDomMocks.mfaRefetch,
      }
    }
    if (options.queryKey?.join(':') === 'auth:security:sessions') {
      return {
        data: {
          sessions: [
            {
              id: 'session-admin-1',
              current: true,
              auth_method: 'local',
              mfa_method: null,
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
        },
        isLoading: false,
        isFetching: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      }
    }
    tokensPageDomMocks.tokenQueryKeys.push(options.queryKey ?? [])
    return {
      data: tokensPageDomMocks.tokensError ? undefined : tokenInventory,
      isLoading: false,
      isFetching: false,
      isError: Boolean(tokensPageDomMocks.tokensError),
      error: tokensPageDomMocks.tokensError,
      refetch: tokensPageDomMocks.tokensRefetch,
    }
  },
  useMutation: (options: {
    mutationKey?: unknown
    onMutate?: (variables: never) => void
    onSuccess?: (data: never, variables: never) => void
    onError?: (error: unknown, variables: never) => void
    onSettled?: (data: never, error: unknown, variables: never) => void
  }) => {
    const mutationKey = Array.isArray(options?.mutationKey)
      ? options.mutationKey.join(':')
      : String(options?.mutationKey ?? '')
    if (mutationKey === 'tokens:revoke') {
      return tokenMutationResult(tokensPageDomMocks.revokeMutate)
    }
    if (mutationKey === 'auth:oidc:reauth:api-token') {
      return tokenMutationResult(tokensPageDomMocks.oidcReauthMutate)
    }
    return tokenMutationResult((payload: unknown) => {
      const record: (typeof tokensPageDomMocks.mutationRecords)[number] = {
        mutationKey: ['tokens', 'create'],
        state: {
          status: 'pending' as const,
          variables: payload,
          error: null as unknown,
        },
      }
      tokensPageDomMocks.mutationRecords.push(record)
      options.onMutate?.(payload as never)
      tokensPageDomMocks.createMutate(payload)
      const error = tokensPageDomMocks.nextCreateError
      tokensPageDomMocks.nextCreateError = null
      if (error) {
        record.state = { ...record.state, status: 'error', error }
        options.onError?.(error, payload as never)
        options.onSettled?.(undefined as never, error, payload as never)
        return
      }
      const data = {
        token: 'tl_secret-created',
        token_prefix: 'tl_created',
        expires_at: '2026-11-25T10:00:00Z',
      }
      record.state = { ...record.state, status: 'success', error: null }
      options.onSuccess?.(data as never, payload as never)
      options.onSettled?.(data as never, null, payload as never)
    })
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => tokensPageDomMocks.currentUser,
}))

vi.mock('react-router-dom', () => ({
  useBlocker: tokensPageDomMocks.useBlocker,
  useLocation: () => ({ pathname: '/settings/tokens', search: '', state: null }),
  useNavigate: () => vi.fn(),
}))

import { TokensPage } from './TokensPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<TokensPage />)
  })
  return container
}

function pageText() {
  return document.body.textContent ?? ''
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

function createMutationVariables() {
  return tokensPageDomMocks.mutationCache
    .findAll({ mutationKey: ['tokens', 'create'] })
    .map((record) => record.state.variables)
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  tokensPageDomMocks.createMutate.mockReset()
  tokensPageDomMocks.revokeMutate.mockReset()
  tokensPageDomMocks.oidcReauthMutate.mockReset()
  tokensPageDomMocks.mfaEnabled = false
  tokensPageDomMocks.mfaError = null
  tokensPageDomMocks.mfaRefetch.mockReset()
  tokensPageDomMocks.tokensError = null
  tokensPageDomMocks.tokensRefetch.mockReset()
  tokensPageDomMocks.tokenQueryKeys.splice(0)
  tokensPageDomMocks.nextCreateError = null
  Object.assign(tokensPageDomMocks.currentUser.data, {
    password_login_enabled: true,
    provisioning_source: 'local',
  })
  Reflect.deleteProperty(tokensPageDomMocks.currentUser.data, 'authentication')
  tokensPageDomMocks.mutationRecords.splice(0)
  tokensPageDomMocks.useBlocker.mockReset()
  tokensPageDomMocks.useBlocker.mockImplementation(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  }))
  Reflect.deleteProperty(navigator, 'clipboard')
})

describe('TokensPage DOM workflows', () => {
  it('shows token ownership for admins and confirms revocation through the dialog', () => {
    const view = renderPage()

    expect(
      view.querySelector('label[for="token-name"]')?.textContent,
    ).toContain('Name')
    expect(
      view.querySelector('label[for="token-current-password"]')?.textContent,
    ).toContain('Current Password')
    expect(pageText()).toContain('Scoped API routes now reject unscoped tokens')
    expect(pageText()).toContain('User ID: viewer-2')
    expect(
      view.querySelectorAll('ul[aria-label="API tokens"] > li'),
    ).toHaveLength(2)
    expect(
      view.querySelector('ul[aria-label="API tokens"] > li p')?.className,
    ).toContain('[overflow-wrap:anywhere]')

    const revokeButton = Array.from(view.querySelectorAll('button')).find(
      (button) =>
        button.textContent?.includes('Revoke') &&
        button.closest('div')?.textContent?.includes('Partner sync'),
    )
    expect(revokeButton).not.toBeNull()

    act(() => {
      revokeButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Revoke API token?')
    expect(pageText()).toContain('Partner sync')
    expect(pageText()).toContain('User ID: viewer-2')
    expect(pageText()).toContain(
      'recursively revokes every delegated child token',
    )

    const confirmRevokeButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Revoke token'))
      .at(-1)
    expect(confirmRevokeButton).not.toBeNull()

    act(() => {
      confirmRevokeButton!.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      )
    })

    expect(tokensPageDomMocks.revokeMutate).toHaveBeenCalledWith('token-2')
  })

  it('validates token creation before sending the request', () => {
    const view = renderPage()
    const nameInput = view.querySelector<HTMLInputElement>('#token-name')
    const expiryInput =
      view.querySelector<HTMLInputElement>('#token-expiry-days')
    const passwordInput = view.querySelector<HTMLInputElement>(
      '#token-current-password',
    )
    const form = view.querySelector('form')

    expect(nameInput).not.toBeNull()
    expect(expiryInput).not.toBeNull()
    expect(passwordInput).not.toBeNull()
    expect(form).not.toBeNull()

    act(() => {
      setInputValue(nameInput!, 'Agent token')
      setInputValue(expiryInput!, '0')
      setInputValue(passwordInput!, 'wrong-password')
    })

    act(() => {
      form!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
    })

    expect(pageText()).toContain('Expiry must be between 1 and 3650 days.')
    expect(tokensPageDomMocks.createMutate).not.toHaveBeenCalled()
  })

  it('requires local MFA for token creation and clears its code after a failed attempt', async () => {
    tokensPageDomMocks.mfaEnabled = true
    tokensPageDomMocks.nextCreateError = new ApiError(
      'The verification code was not accepted.',
      400,
      '/tokens',
      'The verification code was not accepted.',
      { code: 'mfa_code_invalid', requestId: 'token-mfa-123' },
    )
    const view = renderPage()
    const password = view.querySelector<HTMLInputElement>(
      '#token-current-password',
    )!
    const code = view.querySelector<HTMLInputElement>('#token-mfa-code')!

    expect(
      view.querySelector('label[for="token-mfa-code"]')?.textContent,
    ).toContain('Authenticator or recovery code')
    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#token-name')!,
        'MFA-protected token',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#token-expiry-days')!,
        '30',
      )
      setInputValue(password, ' exact password ')
      setInputValue(code, ' recovery-code ')
    })
    await act(async () => {
      view
        .querySelector('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
      await flushPromises()
    })

    expect(tokensPageDomMocks.createMutate).toHaveBeenCalledWith({
      name: 'MFA-protected token',
      expires_in_days: 30,
      current_password: ' exact password ',
      code: 'recovery-code',
    })
    expect(password.value).toBe(' exact password ')
    expect(code.value).toBe('')
    expect(pageText()).toContain('The verification code was not accepted')
    expect(pageText()).toContain(
      'Enter a current authenticator or unused recovery code',
    )
    expect(pageText()).toContain('token-mfa-123')
    await vi.waitFor(() => expect(createMutationVariables()).toHaveLength(0))
  })

  it('blocks token creation when MFA requirements cannot be loaded and offers retry', () => {
    tokensPageDomMocks.mfaError = new ApiError(
      'Security status unavailable',
      503,
      '/auth/security/mfa',
      'Security status unavailable',
      { requestId: 'mfa-status-123', retryable: true },
    )
    const view = renderPage()

    expect(pageText()).toContain(
      'Token security requirements could not be loaded',
    )
    expect(pageText()).toContain('mfa-status-123')
    expect(
      view.querySelector<HTMLButtonElement>('button:not([type="button"])')
        ?.disabled,
    ).toBe(true)
    const retry = [...view.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === 'Retry security check',
    )
    act(() => retry?.click())
    expect(tokensPageDomMocks.mfaRefetch).toHaveBeenCalled()
  })

  it('copies and promptly removes the one-time bearer value from the DOM', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    const view = renderPage()
    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#token-name')!,
        'One-time token',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#token-expiry-days')!,
        '30',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#token-current-password')!,
        'CurrentPassword123!',
      )
    })
    act(() =>
      view
        .querySelector('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        ),
    )
    expect(pageText()).toContain('tl_secret-created')
    expect(tokensPageDomMocks.useBlocker).toHaveBeenLastCalledWith(true)
    const createdHeading = view.querySelector<HTMLElement>('#new-token-heading')
    const creationAnnouncement = view.querySelector<HTMLElement>(
      '#new-token-created-announcement',
    )
    expect(document.activeElement).toBe(createdHeading)
    expect(creationAnnouncement?.getAttribute('role')).toBe('status')
    expect(creationAnnouncement?.getAttribute('aria-live')).toBe('polite')
    expect(creationAnnouncement?.textContent).toContain('API token created')
    expect(creationAnnouncement?.textContent).not.toContain('tl_secret-created')

    await act(async () => {
      ;[...view.querySelectorAll<HTMLButtonElement>('button')]
        .find((button) => button.textContent === 'Copy and clear')
        ?.click()
      await Promise.resolve()
    })

    expect(writeText).toHaveBeenCalledWith('tl_secret-created')
    expect(pageText()).not.toContain('tl_secret-created')
    expect(pageText()).toContain('API token copied and cleared from this page')
  })

  it('keeps token creation unavailable for an untracked legacy SSO session', () => {
    Object.assign(tokensPageDomMocks.currentUser.data, {
      password_login_enabled: false,
      provisioning_source: 'oidc',
    })
    const view = renderPage()

    expect(view.querySelector('#token-current-password')).toBeNull()
    expect(pageText()).toContain(
      'Browser token creation is unavailable for this account',
    )
    expect(pageText()).toContain('Existing scoped API tokens')
  })

  it('uses recent SSO verification instead of an impossible local-password prompt', () => {
    Object.assign(tokensPageDomMocks.currentUser.data, {
      password_login_enabled: true,
      provisioning_source: 'local',
      authentication: {
        credential_kind: 'opaque_session',
        session_id: 'session-admin-1',
        session_auth_method: 'oidc',
        mfa_method: 'external',
        recently_authenticated: true,
        recent_authentication_expires_at: '2099-08-27T10:10:00Z',
        identity_provider_mfa_asserted: true,
        reauthentication_endpoint: '/auth/oidc/reauth',
        security_actions_supported: true,
      },
    })
    const view = renderPage()

    expect(view.textContent).toContain('This SSO session was recently verified')
    expect(view.querySelector('#token-current-password')).toBeNull()
    expect(view.querySelector('#token-name')).not.toBeNull()
  })

  it('blocks SSO token issuance until the tracked session is freshly verified', () => {
    Object.assign(tokensPageDomMocks.currentUser.data, {
      password_login_enabled: false,
      provisioning_source: 'oidc',
      authentication: {
        credential_kind: 'opaque_session',
        session_id: 'session-admin-1',
        session_auth_method: 'oidc',
        mfa_method: 'external',
        recently_authenticated: false,
        recent_authentication_expires_at: null,
        identity_provider_mfa_asserted: true,
        reauthentication_endpoint: '/auth/oidc/reauth',
        security_actions_supported: true,
      },
    })
    const view = renderPage()

    expect(view.querySelector('#token-current-password')).toBeNull()
    expect(view.textContent).toContain(
      'Verify this SSO session with identity-provider MFA before issuing a durable API token',
    )
    expect(
      Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
        (button) => button.textContent === 'Generate Token',
      )?.disabled,
    ).toBe(true)
    act(() => {
      Array.from(view.querySelectorAll<HTMLButtonElement>('button'))
        .find((button) => button.textContent === 'Verify with SSO and MFA')
        ?.click()
    })
    expect(tokensPageDomMocks.oidcReauthMutate).toHaveBeenCalledTimes(1)
  })

  it('keeps SSO token creation blocked when recent authentication lacks MFA assurance', () => {
    Object.assign(tokensPageDomMocks.currentUser.data, {
      password_login_enabled: false,
      provisioning_source: 'oidc',
      authentication: {
        credential_kind: 'opaque_session',
        session_id: 'session-admin-1',
        session_auth_method: 'oidc',
        mfa_method: 'external',
        recently_authenticated: true,
        recent_authentication_expires_at: '2099-08-27T10:10:00Z',
        identity_provider_mfa_asserted: false,
        reauthentication_endpoint: '/auth/oidc/reauth',
        security_actions_supported: true,
      },
    })
    const view = renderPage()
    const generate = Array.from(
      view.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent === 'Generate Token')

    expect(view.textContent).toContain(
      'confirmed a recent sign-in but did not provide MFA assurance',
    )
    expect(view.textContent).toContain('provider ACR and AMR claims')
    expect(
      Array.from(view.querySelectorAll<HTMLButtonElement>('button')).some(
        (button) => button.textContent === 'Verify again with SSO and MFA',
      ),
    ).toBe(true)
    expect(generate?.disabled).toBe(true)

    act(() => {
      view
        .querySelector('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
    })
    expect(tokensPageDomMocks.createMutate).not.toHaveBeenCalled()
  })

  it('announces token inventory failures with retry and uses mobile-safe semantic inventory markup', () => {
    tokensPageDomMocks.tokensError = new ApiError(
      'Token store unavailable',
      503,
      '/tokens',
      'Token store unavailable',
      { requestId: 'token-list-123', retryable: true },
    )
    const view = renderPage()

    expect(
      view.querySelector(
        'section[aria-labelledby="token-inventory-heading"] [role="alert"]',
      )?.textContent,
    ).toContain('token-list-123')
    const retry = [...view.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === 'Retry token inventory',
    )
    act(() => retry?.click())
    expect(tokensPageDomMocks.tokensRefetch).toHaveBeenCalled()
    expect(
      view.querySelector<HTMLInputElement>('#token-admin-user-filter')
        ?.className,
    ).toContain('max-w-full')
  })

  it('validates a complete admin user ID before applying the token filter', () => {
    const view = renderPage()
    const input = view.querySelector<HTMLInputElement>(
      '#token-admin-user-filter',
    )
    const form = input?.closest('form')

    act(() => {
      if (input) setInputValue(input, 'incomplete-id')
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'Enter a complete user ID',
    )
    expect(tokensPageDomMocks.tokenQueryKeys.at(-1)).toEqual(['tokens', ''])

    act(() => {
      if (input)
        setInputValue(input, 'f65e5641-2fb1-4e1f-bbba-a70aef700c73')
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(view.querySelector('[role="alert"]')).toBeNull()
    expect(tokensPageDomMocks.tokenQueryKeys.at(-1)).toEqual([
      'tokens',
      'f65e5641-2fb1-4e1f-bbba-a70aef700c73',
    ])

    act(() => {
      if (input) setInputValue(input, 'different draft')
    })
    expect(view.textContent).toContain(
      'Draft not applied. Results still use user f65e5641-2fb1-4e1f-bbba-a70aef700c73.',
    )
    expect(tokensPageDomMocks.tokenQueryKeys.at(-1)).toEqual([
      'tokens',
      'f65e5641-2fb1-4e1f-bbba-a70aef700c73',
    ])
  })
})

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
