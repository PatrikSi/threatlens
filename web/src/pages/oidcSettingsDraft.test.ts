import { describe, expect, it, vi } from 'vitest'

import { OIDCProviderSettings } from '../types/api'
import {
  buildOIDCImpactReview,
  createOIDCDraft,
  createOIDCRequest,
  diffOIDCSettings,
  overlappingOIDCChanges,
  rebaseOIDCDraft,
  validateOIDCDraft,
  validateOIDCDraftIssue,
} from './oidcSettingsDraft'

const SETTINGS: OIDCProviderSettings = {
  id: 'provider-1',
  configured: true,
  config_revision: 3,
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
    expect(request.role_mappings).toEqual([
      { claim_value: 'soc-analysts', role: 'analyst' },
    ])
    expect(request.require_verified_email).toBe(true)
  })

  it('sends compare-and-create revision zero and configured provider revisions', () => {
    const draft = createOIDCDraft(SETTINGS)

    expect(createOIDCRequest(draft, 3).expected_config_revision).toBe(3)
    expect(createOIDCRequest(draft, 0).expected_config_revision).toBe(0)
    expect(createOIDCRequest(draft)).not.toHaveProperty('expected_config_revision')
  })

  it('requires exact unique role mapping values', () => {
    const draft = createOIDCDraft(SETTINGS)
    draft.roleMappings = [
      { claim_value: 'soc', role: 'viewer' },
      { claim_value: 'soc', role: 'admin' },
    ]
    expect(validateOIDCDraft(draft, true)).toContain('must be unique')
    expect(validateOIDCDraftIssue(draft, true)).toEqual({
      message: 'Role mapping claim values must be unique.',
      fieldId: 'oidc-mapping-1',
    })
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
    vi.stubGlobal('window', {
      location: { origin: 'https://local.example.com' },
    })
    expect(
      createOIDCDraft({ ...SETTINGS, public_base_url: '' }).publicBaseUrl,
    ).toBe('https://local.example.com')
    vi.unstubAllGlobals()
  })

  it('preserves an explicit trusted-email policy', () => {
    const request = createOIDCRequest(
      createOIDCDraft({ ...SETTINGS, require_verified_email: false }),
    )

    expect(request.require_verified_email).toBe(false)
  })

  it('three-way rebases untouched fields while preserving explicit operator edits', () => {
    const baseline = createOIDCDraft(SETTINGS)
    const operator = { ...baseline, name: 'Operator name' }
    const server = { ...baseline, enabled: false, name: 'Server name' }

    const rebased = rebaseOIDCDraft(baseline, operator, server)

    expect(rebased.name).toBe('Operator name')
    expect(rebased.enabled).toBe(false)
    expect(overlappingOIDCChanges(baseline, operator, server)).toEqual([
      expect.objectContaining({
        label: 'Display name',
        previous: 'Server name',
        next: 'Operator name',
      }),
    ])
    expect(diffOIDCSettings(baseline, server)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: 'Display name' }),
        expect.objectContaining({ label: 'Provider status' }),
      ]),
    )
  })

  it('never exposes a replacement client secret in a field-level diff', () => {
    const baseline = createOIDCDraft(SETTINGS)
    const draft = { ...baseline, clientSecret: 'super-secret-value' }

    expect(JSON.stringify(diffOIDCSettings(baseline, draft))).not.toContain(
      'super-secret-value',
    )
    expect(diffOIDCSettings(baseline, draft)).toEqual([
      expect.objectContaining({
        label: 'Client secret',
        next: 'Replacement entered',
      }),
    ])
  })

  it('requires acknowledgement for auto-approved unverified administrator provisioning', () => {
    const baseline = createOIDCDraft(SETTINGS)
    const draft = {
      ...baseline,
      defaultRole: 'admin' as const,
      autoApproveUsers: true,
      requireVerifiedEmail: false,
    }

    const review = buildOIDCImpactReview(baseline, draft)
    expect(review.requiresAcknowledgement).toBe(true)
    expect(
      review.warnings.map((warning) => warning.message).join(' '),
    ).toContain('Critical combination')
  })
})
