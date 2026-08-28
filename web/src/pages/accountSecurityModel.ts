import type { AuthSession, SessionRevocationResponse } from '../types/identity'

export type SessionRevocationAction =
  | { kind: 'single'; sessionId: string }
  | { kind: 'others' }

export type SessionReauthenticationDraft = {
  currentPassword: string
  code: string
}

export function isSessionReauthenticationRequired(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  const candidate = error as Error & { status?: number; code?: string | null }
  return (
    error.name === 'ApiError' &&
    candidate.status === 403 &&
    (candidate.code === 'local_reauthentication_required' ||
      candidate.code === 'oidc_reauthentication_required')
  )
}

export function formatSessionRevocationResult(
  result: SessionRevocationResponse,
): string {
  if (
    result.auth_generation_rotated ||
    (result.other_sessions_revoked ?? 0) > 0
  ) {
    return 'Browser access revoked. This browser session was refreshed, and other signed-in browsers must sign in again.'
  }
  return 'Only the selected browser session was revoked. This browser and all other sessions remain signed in. API tokens are unchanged.'
}

export function describeSessionClient(session: AuthSession): string {
  const userAgent = session.user_agent?.trim()
  if (!userAgent) return 'Unknown browser or client'

  const browser = /Edg\//.test(userAgent)
    ? 'Microsoft Edge'
    : /Firefox\//.test(userAgent)
      ? 'Firefox'
      : /Chrome\//.test(userAgent)
        ? 'Chrome'
        : /Safari\//.test(userAgent)
          ? 'Safari'
          : 'Browser or client'
  const platform = /Android/.test(userAgent)
    ? 'Android'
    : /iPhone|iPad/.test(userAgent)
      ? 'iOS'
      : /Windows/.test(userAgent)
        ? 'Windows'
        : /Macintosh|Mac OS/.test(userAgent)
          ? 'macOS'
          : /Linux/.test(userAgent)
            ? 'Linux'
            : null
  return platform ? `${browser} on ${platform}` : browser
}

export function sessionStatus(
  session: AuthSession,
  now = Date.now(),
): 'current' | 'active' | 'expired' | 'revoked' {
  if (session.revoked_at) return 'revoked'
  if (
    Date.parse(session.idle_expires_at) <= now ||
    Date.parse(session.absolute_expires_at) <= now
  )
    return 'expired'
  return session.current ? 'current' : 'active'
}

export function effectiveSessionExpiry(
  session: Pick<AuthSession, 'idle_expires_at' | 'absolute_expires_at'>,
): string {
  const idleExpiry = Date.parse(session.idle_expires_at)
  const absoluteExpiry = Date.parse(session.absolute_expires_at)
  if (Number.isNaN(idleExpiry)) return session.absolute_expires_at
  if (Number.isNaN(absoluteExpiry)) return session.idle_expires_at
  return idleExpiry <= absoluteExpiry
    ? session.idle_expires_at
    : session.absolute_expires_at
}

export function formatAuthMethod(session: AuthSession): string {
  const primary = session.auth_method === 'oidc' ? 'SSO' : 'Password'
  if (session.mfa_method === 'totp') return `${primary} + authenticator`
  if (session.mfa_method === 'recovery_code')
    return `${primary} + recovery code`
  if (session.mfa_method === 'external') return `${primary} + provider MFA`
  return primary
}

export function downloadRecoveryCodes(
  codes: string[],
  generatedAt: string,
): void {
  const content = [
    'ThreatLens recovery codes',
    `Generated: ${generatedAt}`,
    '',
    ...codes,
    '',
    'Each code can be used once. Store this file securely.',
  ].join('\n')
  const objectUrl = URL.createObjectURL(
    new Blob([content], { type: 'text/plain;charset=utf-8' }),
  )
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = 'threatlens-recovery-codes.txt'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(objectUrl)
}
