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
      access: {
        permissions: ['read:tokens', 'write:tokens'],
      },
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue({}),
  },
  queryClient: {
    invalidateQueries: vi.fn(),
    setQueriesData: vi.fn(),
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
  tokensFetching: false,
  tokensPlaceholder: false,
  inventoryTotal: 2,
  tokensRefetch: vi.fn(),
  tokenQueryKeys: [] as unknown[][],
  revokedDescendantCount: 0,
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
      data: tokensPageDomMocks.tokensError
        ? undefined
        : {
            tokens: tokenInventory,
            total: tokensPageDomMocks.inventoryTotal,
            page: 1,
            page_size: 25,
          },
      isLoading: false,
      isFetching: tokensPageDomMocks.tokensFetching,
      isPlaceholderData: tokensPageDomMocks.tokensPlaceholder,
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
      return tokenMutationResult((tokenId: unknown) => {
        tokensPageDomMocks.revokeMutate(tokenId)
        options.onMutate?.(tokenId as never)
        options.onSuccess?.(
          {
            revokedTokenCount: 1,
            revokedDescendantCount:
              tokensPageDomMocks.revokedDescendantCount,
            rootTokenRevoked: true,
          } as never,
          tokenId as never,
        )
      })
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
  tokensPageDomMocks.queryClient.invalidateQueries.mockReset()
  tokensPageDomMocks.queryClient.setQueriesData.mockReset()
  tokensPageDomMocks.oidcReauthMutate.mockReset()
  tokensPageDomMocks.mfaEnabled = false
  tokensPageDomMocks.mfaError = null
  tokensPageDomMocks.mfaRefetch.mockReset()
  tokensPageDomMocks.tokensError = null
  tokensPageDomMocks.tokensFetching = false
  tokensPageDomMocks.tokensPlaceholder = false
  tokensPageDomMocks.inventoryTotal = 2
  tokensPageDomMocks.tokensRefetch.mockReset()
  tokensPageDomMocks.tokenQueryKeys.splice(0)
  tokensPageDomMocks.revokedDescendantCount = 0
  tokensPageDomMocks.nextCreateError = null
  tokensPageDomMocks.currentUser.isError = false
  Object.assign(tokensPageDomMocks.currentUser.data, {
    role: 'admin',
    password_login_enabled: true,
    provisioning_source: 'local',
    access: {
      permissions: ['read:tokens', 'write:tokens'],
    },
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
  it('fails closed when cached administrator identity cannot be refreshed', () => {
    tokensPageDomMocks.currentUser.isError = true
    const view = renderPage()

    expect(pageText()).toContain('Read-only access')
    expect(pageText()).toContain('permission to manage API tokens')
    expect(view.querySelector('#token-name')).toBeNull()
    expect(view.querySelector('#token-admin-user-filter')).toBeNull()
    expect(pageText()).not.toContain('Owner user ID:')
  })

  it('keeps a Viewer with custom read access on a useful read-only inventory', () => {
    Object.assign(tokensPageDomMocks.currentUser.data, {
      role: 'viewer',
      access: { permissions: ['read:tokens'] },
    })
    const view = renderPage()

    expect(pageText()).toContain('Read-only access')
    expect(pageText()).toContain('permission to manage API tokens')
    expect(pageText()).toContain('Issued tokens')
    expect(pageText()).toContain('Legacy automation')
    expect(pageText()).toContain('Active')
    expect(view.querySelector('#create-api-token')).toBeNull()
    expect(view.querySelector('a[href="#create-api-token"]')).toBeNull()
    expect(
      Array.from(view.querySelectorAll('button')).some(
        (button) => button.textContent?.trim() === 'Revoke',
      ),
    ).toBe(false)
    expect(tokensPageDomMocks.createMutate).not.toHaveBeenCalled()
    expect(tokensPageDomMocks.revokeMutate).not.toHaveBeenCalled()
  })

  it('enables token creation and revocation for a Viewer with custom write access', () => {
    Object.assign(tokensPageDomMocks.currentUser.data, {
      role: 'viewer',
      access: { permissions: ['write:tokens'] },
    })
    const view = renderPage()

    expect(pageText()).not.toContain('Read-only access')
    expect(view.querySelector('#create-api-token')).not.toBeNull()
    expect(view.querySelector('a[href="#create-api-token"]')).not.toBeNull()
    expect(
      Array.from(view.querySelectorAll('button')).some(
        (button) => button.textContent?.trim() === 'Revoke',
      ),
    ).toBe(true)
    expect(view.querySelector('#token-admin-user-filter')).toBeNull()
    expect(pageText()).not.toContain('Owner user ID:')
  })

  it('shows token ownership for admins and confirms revocation through the dialog', () => {
    const view = renderPage()

    expect(pageText()).toContain(
      "Administrators can also inspect and revoke another user's tokens by owner ID.",
    )
    expect(pageText()).not.toContain('Organization administration')
    expect(
      view.querySelector('label[for="token-name"]')?.textContent,
    ).toContain('Token name')
    expect(
      view.querySelector('label[for="token-current-password"]')?.textContent,
    ).toContain('Current password')
    expect(pageText()).toContain('Scoped API routes now reject unscoped tokens')
    expect(pageText()).toContain('Owner user ID: viewer-2')
    const inventoryList = view.querySelector('ul[aria-label="API tokens"]')
    const inventoryRows = view.querySelectorAll(
      'ul[aria-label="API tokens"] > li',
    )
    expect(inventoryRows).toHaveLength(2)
    expect(inventoryList?.className).toContain('divide-y')
    expect(inventoryRows[0]?.className).toContain('py-2.5')
    expect(inventoryRows[0]?.querySelector('dl')).not.toBeNull()
    const ownerValue = inventoryRows[0]?.querySelector('dd.font-mono')
    expect(ownerValue?.previousElementSibling?.className).toContain('block')
    expect(ownerValue?.className).toContain('block')
    expect(ownerValue?.className).toContain('sm:inline')
    expect(pageText()).toContain('2 total')
    expect(
      view.querySelector('nav[aria-label="Token inventory pages"]'),
    ).toBeNull()
    expect(
      view.querySelector('ul[aria-label="API tokens"] > li p')?.className,
    ).toContain('[overflow-wrap:anywhere]')
    const nameInput = view.querySelector<HTMLInputElement>('#token-name')
    const expiryInput = view.querySelector<HTMLInputElement>(
      '#token-expiry-days',
    )
    expect(
      view.querySelector('section[aria-labelledby="create-api-token-heading"]'),
    ).not.toBeNull()
    expect(nameInput?.className).toContain('min-h-11')
    expect(nameInput?.className).toContain('sm:min-h-0')
    expect(expiryInput?.parentElement?.parentElement?.className).toContain(
      'sm:grid-cols-[minmax(0,1fr)_8rem]',
    )

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
    expect(pageText()).toContain('Owner user ID: viewer-2')
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
    expect(tokensPageDomMocks.queryClient.setQueriesData).toHaveBeenCalled()
    const updater = tokensPageDomMocks.queryClient.setQueriesData.mock.calls[0][1]
    const updated = updater({
      tokens: tokenInventory,
      total: tokenInventory.length,
      page: 1,
      page_size: 25,
    })
    expect(updated.tokens[1].revoked_at).not.toBeNull()
  })

  it('shows pagination when the token inventory spans multiple pages', () => {
    tokensPageDomMocks.inventoryTotal = 26
    const view = renderPage()
    const pagination = view.querySelector(
      'nav[aria-label="Token inventory pages"]',
    )

    expect(pagination).not.toBeNull()
    expect(pagination?.textContent).toContain('1-2 of 26 · Page 1 of 2')
    expect(
      Array.from(pagination?.querySelectorAll('button') ?? []).map(
        (button) => button.textContent,
      ),
    ).toEqual(['Previous', 'Next'])
  })

  it('marks an applied owner filter as organization administration scope', () => {
    const view = renderPage()
    const ownerId = '00000000-0000-4000-8000-000000000222'
    const ownerFilter = view.querySelector<HTMLInputElement>(
      '#token-admin-user-filter',
    )

    expect(ownerFilter).not.toBeNull()
    expect(ownerFilter?.closest('form')?.getAttribute('aria-label')).toBe(
      'Filter token inventory by owner',
    )
    expect(ownerFilter?.className).toContain('min-h-11')
    expect(ownerFilter?.className).toContain('sm:min-h-0')
    act(() => {
      setInputValue(ownerFilter!, ownerId)
      ownerFilter
        ?.closest('form')
        ?.dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true }),
        )
    })

    const scopeNotice = view.querySelector(
      '[aria-label="Organization token administration scope"]',
    )
    expect(view.querySelector('h1')?.closest('header')?.textContent).toContain(
      'Personal',
    )
    expect(scopeNotice?.textContent).toContain('Organization administration')
    expect(scopeNotice?.textContent).toContain('Owner-scoped token inventory')
    expect(scopeNotice?.textContent).toContain(ownerId)
    expect(scopeNotice?.textContent).toContain(
      "You can inspect and revoke this user's tokens",
    )
    expect(tokensPageDomMocks.tokenQueryKeys).toContainEqual([
      'tokens',
      'inventory',
      ownerId,
      1,
    ])

    const clear = Array.from(view.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent === 'Clear')
    act(() => clear?.click())

    expect(
      view.querySelector('[aria-label="Organization token administration scope"]'),
    ).toBeNull()
  })

  it('fails closed when recursive revocation cannot refresh descendant state', async () => {
    tokensPageDomMocks.revokedDescendantCount = 2
    tokensPageDomMocks.queryClient.invalidateQueries.mockRejectedValueOnce(
      new Error('refresh unavailable'),
    )
    const view = renderPage()
    const revokeButton = Array.from(view.querySelectorAll('button')).find(
      (button) =>
        button.textContent?.includes('Revoke') &&
        button.closest('div')?.textContent?.includes('Partner sync'),
    )

    act(() => revokeButton?.click())
    const confirm = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Revoke token'))
      .at(-1)
    await act(async () => {
      confirm?.click()
      await flushPromises()
    })

    expect(pageText()).toContain(
      'Revocation actions remain disabled until the inventory is current',
    )
    expect(
      Array.from(
        view.querySelectorAll<HTMLButtonElement>(
          'ul[aria-label="API tokens"] button',
        ),
      ).every((button) => button.disabled),
    ).toBe(true)
  })

  it('validates token creation before sending the request', () => {
    const view = renderPage()
    const nameInput = view.querySelector<HTMLInputElement>('#token-name')
    const expiryInput =
      view.querySelector<HTMLInputElement>('#token-expiry-days')
    const passwordInput = view.querySelector<HTMLInputElement>(
      '#token-current-password',
    )
    const form = tokenCreationForm(view)

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
      tokenCreationForm(view)?.dispatchEvent(
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
      tokenCreationForm(view)?.querySelector<HTMLButtonElement>(
        'button[type="submit"]',
      )?.disabled,
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
      tokenCreationForm(view)?.dispatchEvent(
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
        (button) => button.textContent === 'Create token',
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
    ).find((button) => button.textContent === 'Create token')

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
      tokenCreationForm(view)?.dispatchEvent(
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
    expect(tokensPageDomMocks.tokenQueryKeys.at(-1)).toEqual([
      'tokens',
      'inventory',
      '',
      1,
    ])

    act(() => {
      if (input)
        setInputValue(input, 'f65e5641-2fb1-4e1f-bbba-a70aef700c73')
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(view.querySelector('[role="alert"]')).toBeNull()
    expect(tokensPageDomMocks.tokenQueryKeys.at(-1)).toEqual([
      'tokens',
      'inventory',
      'f65e5641-2fb1-4e1f-bbba-a70aef700c73',
      1,
    ])

    act(() => {
      if (input) setInputValue(input, 'different draft')
    })
    expect(view.textContent).toContain(
      'Draft not applied. Results still use owner f65e5641-2fb1-4e1f-bbba-a70aef700c73.',
    )
    expect(tokensPageDomMocks.tokenQueryKeys.at(-1)).toEqual([
      'tokens',
      'inventory',
      'f65e5641-2fb1-4e1f-bbba-a70aef700c73',
      1,
    ])
  })

  it('makes placeholder inventory read-only and closes a stale revocation target when filtering', () => {
    const view = renderPage()
    const revoke = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.includes('Revoke') && !button.disabled,
    )
    act(() => revoke?.click())
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()

    const input = view.querySelector<HTMLInputElement>('#token-admin-user-filter')!
    tokensPageDomMocks.tokensFetching = true
    tokensPageDomMocks.tokensPlaceholder = true
    act(() => {
      setInputValue(input, 'f65e5641-2fb1-4e1f-bbba-a70aef700c73')
      input
        .closest('form')
        ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    expect(view.textContent).toContain('Previous results are read-only')
    expect(
      Array.from(view.querySelectorAll<HTMLButtonElement>('button')).filter(
        (button) => button.textContent?.includes('Revoke'),
      ).every((button) => button.disabled),
    ).toBe(true)
    expect(tokensPageDomMocks.revokeMutate).not.toHaveBeenCalled()
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

function tokenCreationForm(view: HTMLDivElement) {
  return (
    view.querySelector<HTMLInputElement>('#token-name')?.closest('form') ?? null
  )
}
