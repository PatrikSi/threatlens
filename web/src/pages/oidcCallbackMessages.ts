import { resolveApiErrorMessage } from '../api/errors'

export type OIDCCallbackNotice = {
  message: string
  error: boolean
}

const LOGIN_MESSAGES: Record<string, string> = {
  approval_required: 'Your SSO account is waiting for administrator approval.',
  account_inactive:
    'This ThreatLens account is inactive. Contact an administrator.',
  email_link_required:
    'An account already uses this email. Sign in locally, then link SSO from Account settings.',
  not_provisioned:
    'No ThreatLens account is linked to this identity. Contact an administrator.',
  email_required: 'The identity provider did not supply an email address.',
  invalid_email: 'The identity provider supplied an invalid email address.',
  verified_email_required:
    'The identity provider did not supply a verified email address.',
  provider_rejected:
    'The identity provider did not complete sign-in. Start SSO sign-in again.',
  provider_unavailable:
    'The SSO provider is temporarily unavailable. Try again later or contact an administrator.',
  provider_configuration_changed:
    'The SSO configuration changed while sign-in was in progress. Start a new SSO sign-in attempt.',
  callback_rate_limited:
    'Too many SSO callbacks were received from this network. Wait briefly, then start sign-in again.',
  invalid_state:
    'The SSO request expired or could not be verified. Start sign-in again.',
  reauthentication_failed:
    'The identity provider did not confirm a recent sign-in. Start SSO sign-in again and complete any requested verification.',
  role_sync_blocked:
    'The IdP-mapped role could not be applied safely. An administrator must transfer investigation ownership or preserve another active admin, then retry.',
  role_claim_invalid:
    'The identity provider returned an invalid role claim. Ask an administrator to inspect the configured role claim and provider scopes.',
  access_claim_required:
    'The identity provider did not supply an access claim required by ThreatLens. Ask an administrator to verify the provider scopes and claim mappings.',
  access_claim_invalid:
    'The identity provider returned a configured access claim in an unsupported form. Ask an administrator to inspect the provider claim mapping.',
  access_policy_invalid:
    'The ThreatLens SSO access policy references an unavailable role or group. Ask an administrator to repair the access mapping.',
  access_policy_unavailable:
    'ThreatLens could not verify the SSO access policy. Retry sign-in, then ask an administrator to check the database and IAM logs.',
  access_sync_blocked:
    'SSO access could not be reduced safely because an investigation still depends on this account. Ask an administrator to transfer ownership, then retry sign-in.',
  missing_code:
    'The identity provider returned without an authorization code. Start SSO sign-in again.',
  authentication_failed:
    'ThreatLens could not validate the identity provider response. Start SSO sign-in again or contact an administrator.',
  identity_conflict:
    'This SSO identity conflicts with another ThreatLens account. Contact an administrator.',
  account_missing:
    'The linked ThreatLens account no longer exists. Contact an administrator.',
  provisioning_conflict:
    'ThreatLens could not create or link this SSO account because the identity information conflicts with an existing account.',
}

const LINK_MESSAGES: Record<string, string> = {
  identity_in_use: 'That SSO identity is already linked to another account.',
  account_already_linked: 'This account already has an SSO identity.',
  link_session_expired:
    'The account-linking session expired. Start the link again.',
  invalid_state:
    'The account-linking request expired or could not be verified. Start the link again.',
  provider_configuration_changed:
    'The SSO configuration changed while account linking was in progress. Start the link again.',
  callback_rate_limited:
    'Too many SSO callbacks were received from this network. Wait briefly, then start the link again.',
  provider_rejected:
    'The identity provider cancelled or rejected account linking. Start the link again.',
  missing_code:
    'The identity provider returned without an authorization code. Start the link again.',
  authentication_failed:
    'ThreatLens could not validate the identity-provider response. Start the link again or contact an administrator.',
  reauthentication_failed:
    'The identity provider did not confirm a fresh authentication. Start the link again and complete the provider sign-in prompt.',
}

const REAUTH_MESSAGES: Record<string, string> = {
  provider_configuration_changed:
    'The SSO configuration changed while verification was in progress. Start verification again.',
  callback_rate_limited:
    'Too many SSO callbacks were received from this network. Wait briefly, then start verification again.',
  provider_rejected:
    'The identity provider cancelled or rejected verification. Start verification again.',
  missing_code:
    'The identity provider returned without an authorization code. Start verification again.',
  invalid_state:
    'The verification request expired or could not be verified. Start verification again.',
  reauthentication_failed:
    'The identity provider did not prove a recent sign-in. Start verification again and complete every requested sign-in or MFA prompt.',
  reauth_session_expired:
    'The ThreatLens browser session changed or expired during verification. Sign in again, then retry the privileged action.',
  reauth_identity_mismatch:
    'The verified SSO identity does not match the signed-in ThreatLens account. Retry with the same identity used for this account.',
  authentication_failed:
    'ThreatLens could not validate the identity-provider response. Start verification again or contact an administrator.',
}

const REAUTH_START_MESSAGES: Record<string, string> = {
  browser_session_required:
    'SSO verification must start from an authenticated browser session; an API token cannot authorize this action.',
  opaque_session_required:
    'This browser uses a legacy session that cannot be bound to SSO verification. Sign out, sign in again, and retry the action.',
  oidc_session_required:
    'This browser session was not authenticated with SSO. Sign out, sign in with the identity provider, and retry the action.',
  session_inactive:
    'This browser session is no longer active. Sign in again before retrying the privileged action.',
  oidc_provider_unavailable:
    'No enabled identity provider is available for SSO verification. Ask an administrator to restore the provider before retrying.',
  oidc_reauthentication_start_failed:
    'ThreatLens could not start a fresh provider verification. Retry once, then ask an administrator to test the saved provider configuration.',
}

export function resolveOIDCLoginError(errorCode: string): string {
  return (
    LOGIN_MESSAGES[errorCode] ??
    'SSO sign-in could not be completed. Try again or contact an administrator.'
  )
}

export function resolveOIDCLinkNotice(result: string): OIDCCallbackNotice {
  if (result === 'success') {
    return { message: 'SSO identity linked successfully.', error: false }
  }
  return {
    message:
      LINK_MESSAGES[result] ??
      'The SSO identity could not be linked. Start the link again or contact an administrator.',
    error: true,
  }
}

export function resolveOIDCReauthNotice(result: string): OIDCCallbackNotice {
  if (result === 'success') {
    return {
      message: 'Identity-provider verification completed.',
      error: false,
    }
  }
  return {
    message:
      REAUTH_MESSAGES[result] ??
      'Identity-provider verification could not be completed. Start verification again or contact an administrator.',
    error: true,
  }
}

export function resolveOIDCReauthStartError(error: unknown): string {
  const code = readApiErrorString(error, 'code')
  const message = code ? REAUTH_START_MESSAGES[code] : null
  if (!message) {
    return resolveApiErrorMessage(
      error,
      'SSO verification could not be started',
    )
  }
  const requestId = readApiErrorString(error, 'requestId')
  return requestId ? `${message} Request reference: ${requestId}.` : message
}

function readApiErrorString(
  error: unknown,
  field: 'code' | 'requestId',
): string | null {
  if (!(error instanceof Error) || error.name !== 'ApiError') return null
  const value = (error as unknown as Record<string, unknown>)[field]
  return typeof value === 'string' && value.trim() ? value.trim() : null
}
