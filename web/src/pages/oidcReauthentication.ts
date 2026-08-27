import { apiFetch } from '../api/client'
import type { OIDCStartResponse } from '../types/api'

const CONTINUATION_KEY = 'threatlens.oidc_reauth.continuation.v1'
const MAX_CONTINUATION_AGE_MS = 10 * 60 * 1000

export type OIDCReauthPurpose =
  | 'admin_mfa_reset'
  | 'oidc_provider_update'
  | 'session_revocation'
  | 'api_token_create'

export type OIDCReauthContext = {
  targetUserId?: string
  targetEmail?: string
  reason?: string
  sessionAction?: 'single' | 'others'
  sessionId?: string
  tokenName?: string
  tokenExpiresInDays?: number
  tokenScopes?: string
}

export type OIDCReauthContinuation = {
  returnPath: string
  purpose: OIDCReauthPurpose
  context?: OIDCReauthContext
  createdAt: number
}

export type OIDCReauthNavigationState = {
  oidcReauth: {
    result: string
    purpose: OIDCReauthPurpose
    context?: OIDCReauthContext
  }
}

export async function beginOIDCReauthentication({
  returnPath,
  purpose,
  context,
  redirect = (authorizationUrl) => window.location.assign(authorizationUrl),
}: {
  returnPath: string
  purpose: OIDCReauthPurpose
  context?: OIDCReauthContext
  redirect?: (authorizationUrl: string) => void
}): Promise<void> {
  const safeReturnPath = normalizeOIDCReturnPath(returnPath)
  const response = await apiFetch<OIDCStartResponse>('/auth/oidc/reauth', {
    method: 'POST',
  })
  storeOIDCReauthContinuation({
    returnPath: safeReturnPath,
    purpose,
    context: normalizeContext(context),
    createdAt: Date.now(),
  })
  redirect(response.authorization_url)
}

export function consumeOIDCReauthContinuation(
  result: string | null,
  now = Date.now(),
): {
  continuation: OIDCReauthContinuation
  navigationState: OIDCReauthNavigationState
} | null {
  if (!result || typeof window === 'undefined') return null
  const storage = getSessionStorage()
  if (!storage) return null
  const continuation = readStoredContinuation(storage, now)
  try {
    storage.removeItem(CONTINUATION_KEY)
  } catch {
    // The backend's rotated session remains the authority if browser cleanup is denied.
  }
  if (!continuation) return null
  return {
    continuation,
    navigationState: {
      oidcReauth: {
        result,
        purpose: continuation.purpose,
        context: continuation.context,
      },
    },
  }
}

export function readOIDCReauthNavigationState(
  value: unknown,
  expectedPurpose: OIDCReauthPurpose,
): OIDCReauthNavigationState['oidcReauth'] | null {
  if (!value || typeof value !== 'object' || !('oidcReauth' in value))
    return null
  const candidate = (value as { oidcReauth?: unknown }).oidcReauth
  if (!candidate || typeof candidate !== 'object') return null
  const record = candidate as Record<string, unknown>
  if (record.purpose !== expectedPurpose || typeof record.result !== 'string')
    return null
  return {
    result: record.result,
    purpose: expectedPurpose,
    context: normalizeContext(record.context),
  }
}

export function normalizeOIDCReturnPath(value: string): string {
  const candidate = value.trim()
  if (
    !candidate.startsWith('/settings/') ||
    candidate.startsWith('//') ||
    candidate.includes('\\') ||
    hasUnsafePathCharacters(candidate)
  ) {
    return '/settings/account'
  }
  try {
    const parsed = new URL(candidate, 'https://threatlens.invalid')
    if (
      parsed.origin !== 'https://threatlens.invalid' ||
      !parsed.pathname.startsWith('/settings/')
    ) {
      return '/settings/account'
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch {
    return '/settings/account'
  }
}

function hasUnsafePathCharacters(value: string): boolean {
  return [...value].some((character) => {
    const codePoint = character.codePointAt(0) ?? 0
    return codePoint < 32 || codePoint === 127
  })
}

function storeOIDCReauthContinuation(
  continuation: OIDCReauthContinuation,
): void {
  const storage = getSessionStorage()
  if (!storage) {
    throw new Error(
      'Browser session storage is unavailable. Allow site storage, then start verification again.',
    )
  }
  try {
    storage.setItem(CONTINUATION_KEY, JSON.stringify(continuation))
  } catch {
    throw new Error(
      'Browser session storage could not preserve the privileged action. Free site storage or allow it, then retry.',
    )
  }
}

function readStoredContinuation(
  storage: Storage,
  now: number,
): OIDCReauthContinuation | null {
  try {
    const serialized = storage.getItem(CONTINUATION_KEY)
    if (!serialized) return null
    const candidate = JSON.parse(serialized) as Record<string, unknown>
    if (
      (candidate.purpose !== 'admin_mfa_reset' &&
        candidate.purpose !== 'oidc_provider_update' &&
        candidate.purpose !== 'session_revocation' &&
        candidate.purpose !== 'api_token_create') ||
      typeof candidate.returnPath !== 'string' ||
      typeof candidate.createdAt !== 'number' ||
      now - candidate.createdAt < 0 ||
      now - candidate.createdAt > MAX_CONTINUATION_AGE_MS
    ) {
      return null
    }
    return {
      returnPath: normalizeOIDCReturnPath(candidate.returnPath),
      purpose: candidate.purpose,
      context: normalizeContext(candidate.context),
      createdAt: candidate.createdAt,
    }
  } catch {
    return null
  }
}

function getSessionStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function normalizeContext(value: unknown): OIDCReauthContext | undefined {
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as Record<string, unknown>
  const context: OIDCReauthContext = {}
  if (
    typeof candidate.targetUserId === 'string' &&
    candidate.targetUserId.length <= 64
  ) {
    context.targetUserId = candidate.targetUserId
  }
  if (
    typeof candidate.targetEmail === 'string' &&
    candidate.targetEmail.length <= 320
  ) {
    context.targetEmail = candidate.targetEmail
  }
  if (typeof candidate.reason === 'string' && candidate.reason.length <= 500) {
    context.reason = candidate.reason
  }
  if (candidate.sessionAction === 'single' || candidate.sessionAction === 'others') {
    context.sessionAction = candidate.sessionAction
  }
  if (
    typeof candidate.sessionId === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      candidate.sessionId,
    )
  ) {
    context.sessionId = candidate.sessionId
  }
  if (typeof candidate.tokenName === 'string' && candidate.tokenName.length <= 255) {
    context.tokenName = candidate.tokenName
  }
  if (
    typeof candidate.tokenExpiresInDays === 'number' &&
    Number.isInteger(candidate.tokenExpiresInDays) &&
    candidate.tokenExpiresInDays >= 1 &&
    candidate.tokenExpiresInDays <= 3650
  ) {
    context.tokenExpiresInDays = candidate.tokenExpiresInDays
  }
  if (typeof candidate.tokenScopes === 'string' && candidate.tokenScopes.length <= 2048) {
    context.tokenScopes = candidate.tokenScopes
  }
  return Object.keys(context).length ? context : undefined
}
