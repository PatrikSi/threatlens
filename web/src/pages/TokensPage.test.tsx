import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import {
  createInitialTokenCreateFormState,
  DEFAULT_TOKEN_EXPIRY_DAYS,
  reduceTokenCreateFormState,
} from '../hooks/useTokenCreateFormState'

const tokensPageMocks = vi.hoisted(() => ({
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
    refetch: vi.fn(),
  },
  queryClient: {
    invalidateQueries: vi.fn(),
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => tokensPageMocks.queryClient,
  useQuery: () => ({
    data: [
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
        name: 'Scoped automation',
        token_prefix: 'tl_scoped',
        scopes: ['read:feeds'],
        last_used_at: '2026-04-21T10:00:00Z',
        expires_at: '2026-07-20T10:00:00Z',
        revoked_at: null,
        created_at: '2026-04-20T10:00:00Z',
      },
    ],
    isLoading: false,
    isError: false,
    error: null,
  }),
  useMutation: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => tokensPageMocks.currentUser,
}))

vi.mock('react-router-dom', () => ({
  useBlocker: () => ({
    state: 'unblocked',
    proceed: vi.fn(),
    reset: vi.fn(),
  }),
  useLocation: () => ({ pathname: '/settings/tokens', search: '', state: null }),
  useNavigate: () => vi.fn(),
}))

import { TokensPage } from './TokensPage'

describe('reduceTokenCreateFormState', () => {
  it('clears any previously shown token as soon as a new create attempt starts', () => {
    const startingState = {
      ...createInitialTokenCreateFormState(),
      name: 'Threat bot',
      createdToken: {
        token: 'secret-old',
        token_prefix: 'tl_old',
        expires_at: null,
      },
    }

    expect(
      reduceTokenCreateFormState(startingState, { type: 'createStarted' }),
    ).toMatchObject({
      name: 'Threat bot',
      createdToken: null,
    })
  })

  it('keeps the draft but removes the shown secret when creation fails', () => {
    const startingState = {
      ...createInitialTokenCreateFormState(),
      currentPassword: 'correct horse battery staple',
      code: '123456',
      createdToken: {
        token: 'secret-old',
        token_prefix: 'tl_old',
        expires_at: null,
      },
    }

    expect(
      reduceTokenCreateFormState(startingState, { type: 'createFailed' }),
    ).toMatchObject({
      currentPassword: 'correct horse battery staple',
      createdToken: null,
    })
  })

  it('resets the draft to safe defaults after a successful creation', () => {
    const startingState = {
      ...createInitialTokenCreateFormState(),
      name: 'Threat bot',
      expiresInDays: 30,
      scopesText: 'read:feeds',
      currentPassword: 'correct horse battery staple',
    }

    expect(
      reduceTokenCreateFormState(startingState, {
        type: 'createSucceeded',
        value: {
          token: 'secret-new',
          token_prefix: 'tl_new',
          expires_at: '2026-07-20T10:00:00Z',
        },
      }),
    ).toEqual({
      name: '',
      expiresInDays: DEFAULT_TOKEN_EXPIRY_DAYS,
      scopesText: '',
      currentPassword: '',
      code: '',
      createdToken: {
        token: 'secret-new',
        token_prefix: 'tl_new',
        expires_at: '2026-07-20T10:00:00Z',
      },
    })
  })

  it('clears an MFA code without discarding the retryable token draft', () => {
    const startingState = {
      ...createInitialTokenCreateFormState(),
      name: 'Threat bot',
      currentPassword: 'exact password ',
      code: 'recovery-code',
    }

    expect(
      reduceTokenCreateFormState(startingState, { type: 'clearCode' }),
    ).toMatchObject({
      name: 'Threat bot',
      currentPassword: 'exact password ',
      code: '',
    })
  })
})

describe('TokensPage rendered workflow', () => {
  it('renders explicit token-creation controls and warns on legacy unscoped tokens', () => {
    const markup = renderToStaticMarkup(createElement(TokensPage))

    expect(markup).toContain('API tokens')
    expect(markup).toContain('Create API token')
    expect(markup).toContain(
      'Administrators can also inspect and revoke another user',
    )
    expect(markup).toContain('Current password')
    expect(markup).toContain('Owner user ID')
    expect(markup).toContain(
      'Browser sessions must confirm the current account password',
    )
    expect(markup).toContain('Scoped API routes now reject unscoped tokens')
    expect(markup).toContain('Owner user ID')
    expect(markup).toContain('viewer-2')
  })
})
