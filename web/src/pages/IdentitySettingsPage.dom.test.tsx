// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const identityPageMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('../api/client', () => ({
  apiFetch: identityPageMocks.apiFetch,
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: () => ({
    discardDialog: null,
    confirmDiscard: (action: () => void) => action(),
  }),
}))

import { IdentitySettingsPage } from './IdentitySettingsPage'

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

const providerSettings = {
  id: 'provider-1',
  configured: true,
  name: 'Acme SSO',
  enabled: true,
  issuer_url: 'https://idp.example.com',
  client_id: 'threatlens',
  has_client_secret: true,
  client_auth_method: 'client_secret_basic',
  public_base_url: 'https://threatlens.example.com',
  callback_url: 'https://threatlens.example.com/api/v1/auth/oidc/callback',
  scopes: ['openid', 'profile', 'email', 'groups'],
  role_claim: 'groups',
  role_mappings: [{ claim_value: 'soc-analysts', role: 'analyst' }],
  default_role: 'viewer',
  jit_provisioning_enabled: true,
  auto_approve_users: false,
  sync_roles_on_login: true,
  created_at: '2026-07-31T10:00:00Z',
  updated_at: '2026-07-31T10:00:00Z',
}

function renderPage() {
  identityPageMocks.apiFetch.mockResolvedValue(providerSettings)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <IdentitySettingsPage />
      </QueryClientProvider>,
    )
  })
  return container
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

afterEach(async () => {
  await act(async () => {
    root?.unmount()
    await flushPromises()
  })
  queryClient?.clear()
  root = null
  queryClient = null
  container?.remove()
  container = null
  identityPageMocks.apiFetch.mockReset()
})

describe('IdentitySettingsPage', () => {
  it('renders provider connection, callback, provisioning, and role mapping controls', async () => {
    const view = renderPage()
    await act(async () => {
      await flushPromises()
      await flushPromises()
    })
    await vi.waitFor(() => {
      expect(view.querySelector<HTMLInputElement>('#oidc-name')?.value).toBe('Acme SSO')
    })

    expect(view.querySelector<HTMLInputElement>('#oidc-issuer')?.value).toBe('https://idp.example.com')
    expect(view.querySelector<HTMLInputElement>('#oidc-mapping-0')?.value).toBe('soc-analysts')
    expect(view.textContent).toContain('https://threatlens.example.com/api/v1/auth/oidc/callback')
    expect(view.textContent).toContain('JIT provisioning')
    expect(view.textContent).toContain('Sync roles on sign-in')
  })
})
