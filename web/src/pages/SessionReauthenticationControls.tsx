import { resolveApiErrorMessage } from '../api/errors'
import type { MFAStatusResponse } from '../types/identity'
import type {
  SessionReauthenticationDraft,
  SessionRevocationAction,
} from './accountSecurityModel'
import { resolveOIDCReauthStartError } from './oidcCallbackMessages'

export function SessionReauthenticationControls({
  action,
  authMethod,
  mfaStatus,
  mfaLoading,
  mfaError,
  draft,
  localError,
  localPending,
  oidcError,
  oidcPending,
  onDraftChange,
  onLocalVerify,
  onOIDCVerify,
  onRetryMfa,
}: {
  action: SessionRevocationAction | null
  authMethod?: 'local' | 'oidc'
  mfaStatus?: MFAStatusResponse
  mfaLoading: boolean
  mfaError: unknown
  draft: SessionReauthenticationDraft
  localError: unknown
  localPending: boolean
  oidcError: unknown
  oidcPending: boolean
  onDraftChange: (draft: SessionReauthenticationDraft) => void
  onLocalVerify: () => void
  onOIDCVerify: () => void
  onRetryMfa: () => void
}) {
  if (!action) return null
  const actionLabel =
    action.kind === 'others' ? 'revoking other sessions' : 'revoking this session'

  if (authMethod === 'oidc') {
    return (
      <div className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
        <p className="font-semibold">Recent SSO verification required</p>
        <p className="mt-1">
          Verify with the identity provider, then review and confirm the
          revocation again. ThreatLens will not revoke anything automatically.
        </p>
        <button
          type="button"
          className="mt-3 min-h-11 rounded border border-current px-3 py-2 font-semibold"
          onClick={onOIDCVerify}
          disabled={oidcPending}
        >
          {oidcPending ? 'Starting verification...' : 'Verify with SSO'}
        </button>
        {Boolean(oidcError) && (
          <p role="alert" className="mt-2 text-red-700 dark:text-red-300">
            {resolveOIDCReauthStartError(oidcError)}
          </p>
        )}
      </div>
    )
  }

  const codeRequired = mfaStatus?.enabled === true
  const submitDisabled =
    localPending ||
    mfaLoading ||
    Boolean(mfaError) ||
    !draft.currentPassword ||
    (codeRequired && draft.code.trim().length < 6)
  return (
    <div className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
      <p className="font-semibold">Recent local verification required</p>
      <p className="mt-1">
        Confirm your current credentials before {actionLabel}. Recovery codes
        are not accepted for this verification.
      </p>
      {mfaLoading && (
        <p role="status" className="mt-2">
          Checking MFA requirements...
        </p>
      )}
      {Boolean(mfaError) && (
        <div role="alert" className="mt-2">
          <p>
            {resolveApiErrorMessage(
              mfaError,
              'MFA requirements could not be loaded',
            )}
          </p>
          <button
            type="button"
            className="mt-2 min-h-10 rounded border border-current px-3 py-2 font-semibold"
            onClick={onRetryMfa}
          >
            Retry security check
          </button>
        </div>
      )}
      {!mfaLoading && !mfaError && (
        <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">
          <label
            htmlFor="session-reauth-password"
            className="min-w-0 font-semibold"
          >
            Current password
            <input
              id="session-reauth-password"
              type="password"
              autoComplete="current-password"
              value={draft.currentPassword}
              onChange={(event) =>
                onDraftChange({ ...draft, currentPassword: event.target.value })
              }
              className="mt-1 w-full min-w-0 rounded border border-amber-500/40 bg-white px-3 py-2 text-ink dark:bg-[#072019] dark:text-white"
            />
          </label>
          {codeRequired && (
            <label
              htmlFor="session-reauth-code"
              className="min-w-0 font-semibold"
            >
              Authenticator code
              <input
                id="session-reauth-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={draft.code}
                onChange={(event) =>
                  onDraftChange({ ...draft, code: event.target.value })
                }
                className="mt-1 w-full min-w-0 rounded border border-amber-500/40 bg-white px-3 py-2 font-mono text-ink dark:bg-[#072019] dark:text-white"
              />
            </label>
          )}
        </div>
      )}
      <button
        type="button"
        className="mt-3 min-h-11 rounded border border-current px-3 py-2 font-semibold disabled:opacity-60"
        onClick={onLocalVerify}
        disabled={submitDisabled}
      >
        {localPending ? 'Verifying...' : 'Verify this session'}
      </button>
      {Boolean(localError) && (
        <p role="alert" className="mt-2 text-red-700 dark:text-red-300">
          {resolveApiErrorMessage(
            localError,
            'This session could not be verified',
          )}
        </p>
      )}
    </div>
  )
}
