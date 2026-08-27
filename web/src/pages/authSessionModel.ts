import type {
  AuthMethod,
  AuthSession,
  AuthSessionListResponse,
  CurrentAuthentication,
} from '../types/api'

export type PrivilegedSessionState = {
  tracked: boolean
  authMethod: AuthMethod | null
  recentAuthenticationValid: boolean
  modernContract: boolean
}

export function resolveCurrentSession(
  data?: AuthSessionListResponse,
): AuthSession | null {
  if (!Array.isArray(data?.sessions)) return null
  return (
    data.sessions.find((session) => session.current && !session.revoked_at) ??
    null
  )
}

export function resolvePrivilegedSessionState(
  authentication: CurrentAuthentication | undefined,
  sessions: AuthSessionListResponse | undefined,
  legacyOIDCVerificationSucceeded = false,
): PrivilegedSessionState {
  if (authentication) {
    const tracked =
      authentication.credential_kind === 'opaque_session' &&
      authentication.session_auth_method !== null
    return {
      tracked,
      authMethod: tracked ? authentication.session_auth_method : null,
      recentAuthenticationValid:
        tracked && resolveRecentlyAuthenticated(authentication),
      modernContract: true,
    }
  }

  const currentSession = resolveCurrentSession(sessions)
  return {
    tracked: Boolean(currentSession),
    authMethod: currentSession?.auth_method ?? null,
    recentAuthenticationValid:
      currentSession?.auth_method === 'local' ||
      (currentSession?.auth_method === 'oidc' &&
        legacyOIDCVerificationSucceeded),
    modernContract: false,
  }
}

function resolveRecentlyAuthenticated(
  authentication: CurrentAuthentication,
): boolean {
  if (typeof authentication.recently_authenticated === 'boolean') {
    return authentication.recently_authenticated
  }
  return authentication.recent_authentication_valid === true
}
