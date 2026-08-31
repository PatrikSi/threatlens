import { useEffect, useRef, useState, type FormEvent, type RefObject } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { TokenCreateFormState } from '../hooks/useTokenCreateFormState'
import type { MFAStatusResponse } from '../types/api'
import { resolveOIDCReauthStartError } from './oidcCallbackMessages'

export function TokenCreatePanel({
  state,
  formError,
  requestError,
  mfaStatus,
  mfaLoading,
  mfaError,
  mfaFetching,
  creationAvailable,
  currentAuthMethod,
  oidcRecentlyAuthenticated,
  oidcReauthPending,
  oidcReauthError,
  createPending,
  nameInputRef,
  expiryInputRef,
  passwordInputRef,
  codeInputRef,
  onSubmit,
  onNameChange,
  onExpiryChange,
  onScopesChange,
  onPasswordChange,
  onCodeChange,
  onRetryMfa,
  onOIDCReauthenticate,
  onCreatedTokenStored,
}: {
  state: TokenCreateFormState
  formError: string
  requestError: string
  mfaStatus?: MFAStatusResponse
  mfaLoading: boolean
  mfaError: unknown
  mfaFetching: boolean
  creationAvailable: boolean
  currentAuthMethod?: 'local' | 'oidc'
  oidcRecentlyAuthenticated: boolean
  oidcReauthPending: boolean
  oidcReauthError: unknown
  createPending: boolean
  nameInputRef: RefObject<HTMLInputElement | null>
  expiryInputRef: RefObject<HTMLInputElement | null>
  passwordInputRef: RefObject<HTMLInputElement | null>
  codeInputRef: RefObject<HTMLInputElement | null>
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onNameChange: (value: string) => void
  onExpiryChange: (value: number) => void
  onScopesChange: (value: string) => void
  onPasswordChange: (value: string) => void
  onCodeChange: (value: string) => void
  onRetryMfa: () => void
  onOIDCReauthenticate: () => void
  onCreatedTokenStored: (method: 'copied' | 'acknowledged') => void
}) {
  const [copyError, setCopyError] = useState('')
  const currentUserQuery = useCurrentUser()
  const createdTokenHeadingRef = useRef<HTMLHeadingElement | null>(null)
  const oidcMfaAssured =
    currentUserQuery.data?.authentication?.identity_provider_mfa_asserted === true
  const oidcTokenCreationReady =
    oidcRecentlyAuthenticated && oidcMfaAssured

  useEffect(() => {
    if (state.createdToken) createdTokenHeadingRef.current?.focus()
  }, [state.createdToken])

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    if (currentAuthMethod === 'oidc' && !oidcTokenCreationReady) {
      event.preventDefault()
      return
    }
    onSubmit(event)
  }

  const copyAndClear = async () => {
    if (!state.createdToken) return
    try {
      await navigator.clipboard.writeText(state.createdToken.token)
      setCopyError('')
      onCreatedTokenStored('copied')
    } catch {
      setCopyError(
        'Clipboard access was denied. Select and copy the token manually, then acknowledge it below.',
      )
    }
  }
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h2 className="font-display text-xl">Create API token</h2>
      <p className="mt-1 text-sm text-slate dark:text-slate-300">
        Token value is only shown once after creation.
      </p>
      {!creationAvailable ? (
        <div
          role="status"
          className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
        >
          <p className="font-semibold">
            Browser token creation is unavailable for this account
          </p>
          <p className="mt-1">
            Sign out and sign in again to establish a tracked browser session,
            then retry. Existing scoped API tokens can continue delegating
            short-lived child tokens through the API.
          </p>
        </div>
      ) : (
        <>
          {currentAuthMethod === 'oidc' ? (
            <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              <p>
                {oidcTokenCreationReady
                  ? 'This SSO session was recently verified with identity-provider MFA. Token issuance remains bound to this account and the selected scopes.'
                  : oidcRecentlyAuthenticated
                    ? 'The identity provider confirmed a recent sign-in but did not provide MFA assurance. Complete MFA at the identity provider, then verify again. If this continues, ask an administrator to check the provider ACR and AMR claims.'
                    : 'Verify this SSO session with identity-provider MFA before issuing a durable API token. Your token name, expiry, and scopes are restored after the provider redirect.'}
              </p>
              {!oidcTokenCreationReady && (
                <button
                  type="button"
                  className="mt-3 min-h-11 rounded border border-current px-3 py-2 font-semibold"
                  onClick={onOIDCReauthenticate}
                  disabled={oidcReauthPending}
                >
                  {oidcReauthPending
                    ? 'Starting verification...'
                    : oidcRecentlyAuthenticated
                      ? 'Verify again with SSO and MFA'
                      : 'Verify with SSO and MFA'}
                </button>
              )}
              {Boolean(oidcReauthError) && (
                <p role="alert" className="mt-2 text-red-700 dark:text-red-300">
                  {resolveOIDCReauthStartError(oidcReauthError)}
                </p>
              )}
            </div>
          ) : (
            <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              Browser sessions must confirm the current account password before
              creating a durable API token.
              {mfaStatus?.enabled &&
                ' Local MFA also requires a current authenticator or recovery code.'}
            </div>
          )}
          <form className="mt-3 space-y-3" onSubmit={handleSubmit} noValidate>
            <div>
              <label htmlFor="token-name" className="text-sm font-semibold">
                Token name
              </label>
              <input
                ref={nameInputRef}
                id="token-name"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={state.name}
                onChange={(event) => onNameChange(event.target.value)}
                required
              />
            </div>
            <div>
              <label
                htmlFor="token-expiry-days"
                className="text-sm font-semibold"
              >
                Expires after (days)
              </label>
              <input
                ref={expiryInputRef}
                id="token-expiry-days"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                type="number"
                min={1}
                max={3650}
                value={state.expiresInDays}
                onChange={(event) => onExpiryChange(Number(event.target.value))}
                required
              />
            </div>
            <div>
              <label htmlFor="token-scopes" className="text-sm font-semibold">
                Permissions (API scopes)
              </label>
              <input
                id="token-scopes"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={state.scopesText}
                onChange={(event) => onScopesChange(event.target.value)}
                placeholder="read:feeds,write:items"
              />
              <p className="mt-1 text-xs text-slate dark:text-slate-400">
                Leave blank to use the recommended read-only permissions. To
                specify permissions, enter comma-separated API scope names.
              </p>
            </div>
            {currentAuthMethod !== 'oidc' && (
              <div>
                <label
                  htmlFor="token-current-password"
                  className="text-sm font-semibold"
                >
                  Current password
                </label>
                <input
                  ref={passwordInputRef}
                  id="token-current-password"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  type="password"
                  autoComplete="current-password"
                  value={state.currentPassword}
                  onChange={(event) => onPasswordChange(event.target.value)}
                  required
                />
              </div>
            )}
            {currentAuthMethod !== 'oidc' && mfaLoading && (
              <p
                role="status"
                className="text-sm text-slate dark:text-slate-300"
              >
                Checking local MFA requirements...
              </p>
            )}
            {currentAuthMethod !== 'oidc' && Boolean(mfaError) && (
              <div
                role="alert"
                className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
              >
                <p>
                  {resolveApiErrorMessage(
                    mfaError,
                    'Token security requirements could not be loaded',
                  )}
                </p>
                <button
                  type="button"
                  className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
                  onClick={onRetryMfa}
                  disabled={mfaFetching}
                >
                  {mfaFetching ? 'Retrying...' : 'Retry security check'}
                </button>
              </div>
            )}
            {currentAuthMethod !== 'oidc' && mfaStatus?.enabled && (
              <div>
                <label
                  htmlFor="token-mfa-code"
                  className="text-sm font-semibold"
                >
                  Authenticator or recovery code
                </label>
                <input
                  ref={codeInputRef}
                  id="token-mfa-code"
                  type="text"
                  inputMode="text"
                  autoComplete="one-time-code"
                  autoCapitalize="none"
                  spellCheck={false}
                  maxLength={128}
                  value={state.code}
                  onChange={(event) => onCodeChange(event.target.value)}
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono dark:border-cyan-900/40 dark:bg-[#072019]"
                  aria-describedby="token-mfa-code-help"
                  required
                />
                <p
                  id="token-mfa-code-help"
                  className="mt-1 text-xs text-slate dark:text-slate-300"
                >
                  Recovery codes are single use. Spaces and hyphens are accepted
                  when present in the code.
                </p>
              </div>
            )}
            <button
              type="submit"
              className="rounded bg-ink px-3 py-2 text-white disabled:cursor-not-allowed disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e]"
              disabled={
                createPending ||
                (currentAuthMethod === 'oidc'
                  ? !oidcTokenCreationReady
                  : mfaLoading || Boolean(mfaError) || !mfaStatus)
              }
            >
              Create token
            </button>
            {formError && (
              <p
                role="alert"
                aria-live="assertive"
                aria-atomic="true"
                className="text-sm text-red-600 dark:text-red-300"
              >
                {formError}
              </p>
            )}
            {requestError && !formError && (
              <p
                role="alert"
                aria-live="assertive"
                aria-atomic="true"
                className="text-sm text-red-600"
              >
                {requestError}
              </p>
            )}
          </form>

          {state.createdToken && (
            <div className="mt-4 rounded border border-cyan/40 bg-cyan/10 p-3 text-sm dark:bg-cyan/15">
              <p
                id="new-token-created-announcement"
                role="status"
                aria-live="polite"
                aria-atomic="true"
                className="sr-only"
              >
                API token created. Focus moved to the one-time token controls.
              </p>
              <div role="region" aria-labelledby="new-token-heading">
                <h3
                  ref={createdTokenHeadingRef}
                  id="new-token-heading"
                  tabIndex={-1}
                  className="font-semibold outline-none focus-visible:ring-2 focus-visible:ring-cyan"
                >
                  New token created
                </h3>
                <p className="mt-1 text-xs">
                  Store this value now. It will be removed from this page as soon
                  as you copy or acknowledge it.
                </p>
                <p className="mt-1 break-all font-mono text-xs">
                  {state.createdToken.token}
                </p>
                <p className="mt-1 text-xs text-slate dark:text-slate-300">
                  Prefix: {state.createdToken.token_prefix}
                </p>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                  <button
                    type="button"
                    className="min-h-11 rounded border border-current px-3 py-2 font-semibold"
                    onClick={() => void copyAndClear()}
                  >
                    Copy and clear
                  </button>
                  <button
                    type="button"
                    className="min-h-11 rounded border border-slate/30 px-3 py-2 font-semibold dark:border-white/20"
                    onClick={() => onCreatedTokenStored('acknowledged')}
                  >
                    I stored this token
                  </button>
                </div>
                {copyError && (
                  <p
                    role="alert"
                    className="mt-2 text-xs text-red-600 dark:text-red-300"
                  >
                    {copyError}
                  </p>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}
