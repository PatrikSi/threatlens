import { describe, expect, it } from 'vitest'

import type { AdminUser } from '../types/api'
import {
  buildUserDirectoryPath,
  formatAuthenticationMethods,
  formatUserUpdateSuccess,
  hasAvailableSignInMethod,
  resolveAccountLabel,
  resolveCredentialManagementSource,
} from './userDirectoryModel'

const LOCAL_USER: AdminUser = {
  id: 'user-1',
  email: 'analyst@example.com',
  role: 'analyst',
  is_active: true,
  is_approved: true,
  approved_at: '2026-08-27T10:00:00Z',
  created_at: '2026-08-26T10:00:00Z',
  password_login_enabled: true,
  provisioning_source: 'local',
  authentication_methods: ['password'],
  oidc_provider_name: null,
  oidc_linked_at: null,
  oidc_last_login_at: null,
  password_managed_by: 'local',
  role_managed_by: 'local',
  identity_linked: false,
  sso_sign_in_available: false,
  oidc_identity_status: 'not_linked',
  credential_management_source: 'local',
  mfa_enabled: false,
  mfa_confirmed_at: null,
  active_session_count: 0,
}

describe('user directory model', () => {
  it('builds the paginated directory query with supported server filters', () => {
    expect(
      buildUserDirectoryPath({
        search: ' analyst+one@example.com ',
        role: 'analyst',
        provisioningSource: 'oidc',
        limit: 100,
        offset: 200,
      }),
    ).toBe(
      '/users/directory?limit=100&offset=200&q=analyst%2Bone%40example.com&role=analyst&provisioning_source=oidc',
    )
  })

  it('distinguishes a linked but unavailable identity while retaining local password access', () => {
    const linkedUnavailable: AdminUser = {
      ...LOCAL_USER,
      authentication_methods: ['password', 'oidc'],
      oidc_provider_name: 'Authentik',
      identity_linked: true,
      oidc_identity_status: 'linked_unavailable',
      sso_sign_in_available: false,
    }

    expect(resolveAccountLabel(linkedUnavailable)).toBe('Local + SSO')
    expect(formatAuthenticationMethods(linkedUnavailable)).toBe(
      'Password + SSO linked, currently unavailable',
    )
    expect(hasAvailableSignInMethod(linkedUnavailable)).toBe(true)
  })

  it('uses the authoritative credential-management source over legacy fields', () => {
    const oidcManaged: AdminUser = {
      ...LOCAL_USER,
      authentication_methods: ['oidc'],
      password_managed_by: 'local',
      credential_management_source: 'oidc',
      identity_linked: true,
      oidc_identity_status: 'linked_available',
      sso_sign_in_available: true,
    }

    expect(resolveCredentialManagementSource(oidcManaged)).toBe('oidc')
    expect(formatAuthenticationMethods(oidcManaged)).toBe('SSO available')
    expect(hasAvailableSignInMethod(oidcManaged)).toBe(true)
  })

  it('reports authoritative credential-revocation counts after an admin change', () => {
    expect(
      formatUserUpdateSuccess(
        {
          ...LOCAL_USER,
          credentials_rotated: true,
          revoked_auth_sessions: 2,
          revoked_api_tokens: 1,
        },
        { role: 'admin' },
      ),
    ).toBe(
      'User settings updated. 2 browser sessions revoked and 1 API token revoked.',
    )
  })
})
