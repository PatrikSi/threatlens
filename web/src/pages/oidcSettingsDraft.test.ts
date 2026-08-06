import { describe, expect, it, vi } from 'vitest'

import { OIDCProviderSettings } from '../types/api'
import { createOIDCDraft, createOIDCRequest, validateOIDCDraft } from './oidcSettingsDraft'

const SETTINGS: OIDCProviderSettings = {
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
  callback_path: '/api/v1/auth/oidc/callback',
  scopes: ['openid', 'profile', 'email', 'groups'],
  role_claim: 'groups',
  role_mappings: [{ claim_value: 'soc-analysts', role: 'analyst' }],
  default_role: 'viewer',
  jit_provisioning_enabled: true,
  auto_approve_users: false,
  require_verified_email: true,
  sync_roles_on_login: true,
  created_at: '2026-07-31T10:00:00Z',
  updated_at: '2026-07-31T10:00:00Z',
}

describe('OIDC settings draft', () => {
  it('keeps stored secrets write-only when creating an update request', () => {
    const request = createOIDCRequest(createOIDCDraft(SETTINGS))
    expect(request).not.toHaveProperty('client_secret')
    expect(request.role_mappings).toEqual([{ claim_value: 'soc-analysts', role: 'analyst' }])
    expect(request.require_verified_email).toBe(true)
  })

  it('requires exact unique role mapping values', () => {
    const draft = createOIDCDraft(SETTINGS)
    draft.roleMappings = [
      { claim_value: 'soc', role: 'viewer' },
      { claim_value: 'soc', role: 'admin' },
    ]
    expect(validateOIDCDraft(draft, true)).toContain('must be unique')
  })

  it('requires a secret when enabling a confidential client without one stored', () => {
    const draft = createOIDCDraft({ ...SETTINGS, has_client_secret: false })
    expect(validateOIDCDraft(draft, false)).toContain('Enter a client secret')
    draft.clientSecret = 'new-secret'
    expect(validateOIDCDraft(draft, false)).toBeNull()
  })

  it('preserves an explicitly entered client secret as opaque data', () => {
    const draft = createOIDCDraft(SETTINGS)
    draft.clientSecret = '  opaque secret  '

    expect(createOIDCRequest(draft).client_secret).toBe('  opaque secret  ')
  })

  it('defaults a new public URL from the browser origin', () => {
    vi.stubGlobal('window', { location: { origin: 'https://local.example.com' } })
    expect(createOIDCDraft({ ...SETTINGS, public_base_url: '' }).publicBaseUrl).toBe('https://local.example.com')
    vi.unstubAllGlobals()
  })

  it('preserves an explicit trusted-email policy', () => {
    const request = createOIDCRequest(createOIDCDraft({ ...SETTINGS, require_verified_email: false }))

    expect(request.require_verified_email).toBe(false)
  })
})
