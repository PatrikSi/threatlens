import { resolveApiErrorMessage } from '../api/errors'
import type {
  AdminUser,
  AdminUserUpdateResponse,
  User,
  UserCreateRequest,
  UserUpdateRequest,
} from '../types/api'
import type { UserSettingsDraft } from './userSettingsDraft'

export type UserRoleFilter = 'all' | User['role']
export type UserProvisioningFilter = 'all' | 'local' | 'oidc'

export function buildUserDirectoryPath({
  search,
  role,
  provisioningSource,
  limit,
  offset,
}: {
  search: string
  role: UserRoleFilter
  provisioningSource: UserProvisioningFilter
  limit: number
  offset: number
}): string {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })
  if (search.trim()) params.set('q', search.trim())
  if (role !== 'all') params.set('role', role)
  if (provisioningSource !== 'all')
    params.set('provisioning_source', provisioningSource)
  return `/users/directory?${params.toString()}`
}

export function resolveAccountLabel(user: AdminUser): string {
  const category = resolveAccountCategory(user)
  if (category === 'oidc') return 'SSO-provisioned'
  if (category === 'hybrid') return 'Local + SSO'
  return 'Local'
}

export function formatAuthenticationMethods(user: AdminUser): string {
  const methods: string[] = []
  if (
    user.authentication_methods.includes('password') &&
    resolveCredentialManagementSource(user) === 'local'
  ) {
    methods.push('Password')
  }
  const identityStatus = user.oidc_identity_status
  if (
    identityStatus === 'linked_available' ||
    (identityStatus === undefined &&
      user.authentication_methods.includes('oidc'))
  ) {
    methods.push('SSO available')
  } else if (identityStatus === 'linked_unavailable') {
    methods.push('SSO linked, currently unavailable')
  }
  return methods.length > 0 ? methods.join(' + ') : 'None'
}

export function resolveCredentialManagementSource(
  user: AdminUser,
): 'local' | 'oidc' {
  return user.credential_management_source ?? user.password_managed_by
}

export function hasAvailableSignInMethod(user: AdminUser): boolean {
  const passwordAvailable =
    user.password_login_enabled &&
    resolveCredentialManagementSource(user) === 'local' &&
    user.authentication_methods.includes('password')
  const ssoAvailable =
    user.sso_sign_in_available ?? user.authentication_methods.includes('oidc')
  return passwordAvailable || ssoAvailable
}

export function resolveUsersError(error: unknown): string {
  return resolveApiErrorMessage(error, 'User directory could not be loaded')
}

export function resolveUsersMutationError(error: unknown): string {
  if (isUserSecurityVersionConflict(error)) {
    return 'This account changed while your draft was open. The latest server values are being loaded. Review any highlighted overlap, then reapply the intended change.'
  }
  return resolveApiErrorMessage(error, 'User changes could not be saved')
}

export function isUserSecurityVersionConflict(error: unknown): boolean {
  if (!(error instanceof Error) || error.name !== 'ApiError') return false
  const code = (error as { code?: unknown }).code
  return (
    code === 'user_security_version_conflict' ||
    code === 'user_security_version_required'
  )
}

export function formatUserUpdateSuccess(
  result: AdminUserUpdateResponse,
  body: UserUpdateRequest,
): string {
  const prefix = body.password ? 'Password updated.' : 'User settings updated.'
  if (!userUpdateRevokesCredentials(body)) return prefix

  const hasSessionCount = typeof result.revoked_auth_sessions === 'number'
  const hasTokenCount = typeof result.revoked_api_tokens === 'number'
  if (!hasSessionCount && !hasTokenCount) {
    return `${prefix} Existing browser sessions and API tokens were revoked.`
  }

  const details: string[] = []
  if (hasSessionCount) {
    const count = result.revoked_auth_sessions as number
    details.push(`${count} browser session${count === 1 ? '' : 's'} revoked`)
  }
  if (hasTokenCount) {
    const count = result.revoked_api_tokens as number
    details.push(`${count} API token${count === 1 ? '' : 's'} revoked`)
  }
  return `${prefix} ${details.join(' and ')}.`
}

export function hasDirtyUserSettingsDrafts(
  draftsByUserId: Record<string, UserSettingsDraft>,
  baselinesByUserId: Record<string, UserSettingsDraft>,
): boolean {
  return Object.entries(draftsByUserId).some(([userId, draft]) => {
    const baseline = baselinesByUserId[userId]
    return Boolean(
      baseline &&
      (draft.role !== baseline.role ||
        draft.isActive !== baseline.isActive ||
        draft.isApproved !== baseline.isApproved),
    )
  })
}

export function isCreateUserFormDirty(form: UserCreateRequest): boolean {
  return (
    form.email !== '' ||
    form.password !== '' ||
    form.role !== 'viewer' ||
    !form.is_active ||
    !form.is_approved
  )
}

function resolveAccountCategory(user: AdminUser): 'local' | 'oidc' | 'hybrid' {
  if (user.provisioning_source === 'oidc') return 'oidc'
  const linked =
    user.identity_linked ?? user.authentication_methods.includes('oidc')
  return linked ? 'hybrid' : 'local'
}

function userUpdateRevokesCredentials(body: UserUpdateRequest): boolean {
  return (
    body.password !== undefined ||
    body.role !== undefined ||
    body.is_active !== undefined ||
    body.is_approved !== undefined
  )
}
