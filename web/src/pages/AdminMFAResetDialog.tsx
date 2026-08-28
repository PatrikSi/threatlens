import { useRef } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type {
  AdminUser,
  AuthSessionListResponse,
  CurrentAuthentication,
  MFAStatusResponse,
} from '../types/api'
import type { OIDCCallbackNotice } from './oidcCallbackMessages'
import { resolvePrivilegedSessionState } from './authSessionModel'

export type AdminMFAResetDraft = {
  reason: string
  currentPassword: string
  code: string
}

type AdminMFAResetDialogProps = {
  target: AdminUser | null
  draft: AdminMFAResetDraft
  ownMfa: MFAStatusResponse | undefined
  currentAuthentication: CurrentAuthentication | undefined
  ownMfaLoading: boolean
  ownMfaError: unknown
  sessions: AuthSessionListResponse | undefined
  sessionsLoading: boolean
  sessionsFetching: boolean
  sessionsError: unknown
  reauthNotice: OIDCCallbackNotice | null
  reauthPending: boolean
  errorMessage: string
  pending: boolean
  onRetryOwnMfa: () => void
  onRetrySessions: () => void
  onStartOIDCReauth: () => void
  onDraftChange: (draft: AdminMFAResetDraft) => void
  onCancel: () => void
  onConfirm: () => void
}

export function AdminMFAResetDialog({
  target,
  draft,
  ownMfa,
  currentAuthentication,
  ownMfaLoading,
  ownMfaError,
  sessions,
  sessionsLoading,
  sessionsFetching,
  sessionsError,
  reauthNotice,
  reauthPending,
  errorMessage,
  pending,
  onRetryOwnMfa,
  onRetrySessions,
  onStartOIDCReauth,
  onDraftChange,
  onCancel,
  onConfirm,
}: AdminMFAResetDialogProps) {
  const reasonInputRef = useRef<HTMLTextAreaElement | null>(null)
  const sessionState = resolvePrivilegedSessionState(
    currentAuthentication,
    sessions,
    reauthNotice?.error === false,
  )
  const localSession = sessionState.authMethod === 'local'
  const oidcSession = sessionState.authMethod === 'oidc'
  const localMfaAssuranceAvailable = ownMfa?.enabled === true
  const oidcVerified =
    oidcSession &&
    sessionState.recentAuthenticationValid &&
    (sessionState.modernContract
      ? currentAuthentication?.identity_provider_mfa_asserted === true
      : reauthNotice?.error === false)
  const fallbackSessionRequired = !sessionState.modernContract
  const localAssuranceRequired = sessionState.authMethod === 'local'
  const securityStateUnavailable =
    (localAssuranceRequired && ownMfaLoading) ||
    (fallbackSessionRequired && sessionsLoading) ||
    (localAssuranceRequired && Boolean(ownMfaError)) ||
    (fallbackSessionRequired && Boolean(sessionsError)) ||
    !sessionState.tracked
  const confirmDisabled =
    securityStateUnavailable ||
    draft.reason.trim().length < 3 ||
    (localSession &&
      (!draft.currentPassword ||
        !draft.code.trim() ||
        !localMfaAssuranceAvailable)) ||
    (oidcSession && !oidcVerified)
  return (
    <ConfirmDialog
      open={Boolean(target)}
      title="Reset multi-factor authentication?"
      description={
        target
          ? `Remove the local authenticator enrollment for ${target.email}? All of their browser sessions and API tokens will be revoked.`
          : undefined
      }
      confirmLabel="Reset MFA and revoke access"
      isConfirming={pending}
      confirmDisabled={confirmDisabled}
      initialFocusRef={reasonInputRef}
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      <div className="space-y-3 text-sm">
        <div>
          <label htmlFor="admin-mfa-reset-reason" className="font-semibold">
            Recovery reason
          </label>
          <textarea
            ref={reasonInputRef}
            id="admin-mfa-reset-reason"
            rows={3}
            maxLength={500}
            value={draft.reason}
            onChange={(event) =>
              onDraftChange({ ...draft, reason: event.target.value })
            }
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            placeholder="Lost device, inaccessible authenticator, support case..."
          />
        </div>
        <AdminMFAResetVerification
          authMethod={sessionState.authMethod}
          tracked={sessionState.tracked}
          fallbackSessionRequired={fallbackSessionRequired}
          draft={draft}
          ownMfaLoading={ownMfaLoading}
          ownMfaError={ownMfaError}
          sessionsLoading={sessionsLoading}
          sessionsFetching={sessionsFetching}
          sessionsError={sessionsError}
          localMfaAssuranceAvailable={localMfaAssuranceAvailable}
          oidcVerified={oidcVerified}
          reauthNotice={reauthNotice}
          reauthPending={reauthPending}
          errorMessage={errorMessage}
          onRetryOwnMfa={onRetryOwnMfa}
          onRetrySessions={onRetrySessions}
          onStartOIDCReauth={onStartOIDCReauth}
          onDraftChange={onDraftChange}
        />
      </div>
    </ConfirmDialog>
  )
}

function AdminMFAResetVerification({
  authMethod,
  tracked,
  fallbackSessionRequired,
  draft,
  ownMfaLoading,
  ownMfaError,
  sessionsLoading,
  sessionsFetching,
  sessionsError,
  localMfaAssuranceAvailable,
  oidcVerified,
  reauthNotice,
  reauthPending,
  errorMessage,
  onRetryOwnMfa,
  onRetrySessions,
  onStartOIDCReauth,
  onDraftChange,
}: {
  authMethod: 'local' | 'oidc' | null
  tracked: boolean
  fallbackSessionRequired: boolean
  draft: AdminMFAResetDraft
  ownMfaLoading: boolean
  ownMfaError: unknown
  sessionsLoading: boolean
  sessionsFetching: boolean
  sessionsError: unknown
  localMfaAssuranceAvailable: boolean
  oidcVerified: boolean
  reauthNotice: OIDCCallbackNotice | null
  reauthPending: boolean
  errorMessage: string
  onRetryOwnMfa: () => void
  onRetrySessions: () => void
  onStartOIDCReauth: () => void
  onDraftChange: (draft: AdminMFAResetDraft) => void
}) {
  return (
    <>
      {authMethod === 'local' && (
        <LocalAdminVerification
          draft={draft}
          assuranceAvailable={localMfaAssuranceAvailable}
          loading={ownMfaLoading}
          error={ownMfaError}
          onDraftChange={onDraftChange}
        />
      )}
      {authMethod === 'oidc' && (
        <OIDCAdminVerification
          draft={draft}
          verified={oidcVerified}
          notice={reauthNotice}
          pending={reauthPending}
          onStart={onStartOIDCReauth}
        />
      )}
      {(ownMfaLoading || (fallbackSessionRequired && sessionsLoading)) && (
        <p role="status" className="text-slate dark:text-slate-300">
          Checking the current administrator session and assurance...
        </p>
      )}
      {authMethod === 'local' && Boolean(ownMfaError) && (
        <RetryError
          message={resolveApiErrorMessage(
            ownMfaError,
            'Administrator MFA requirements could not be loaded',
          )}
          label="Retry MFA check"
          onRetry={onRetryOwnMfa}
        />
      )}
      {fallbackSessionRequired && Boolean(sessionsError) && (
        <RetryError
          message={resolveApiErrorMessage(
            sessionsError,
            'The current administrator session could not be loaded',
          )}
          label={sessionsFetching ? 'Retrying...' : 'Retry session check'}
          onRetry={onRetrySessions}
          disabled={sessionsFetching}
        />
      )}
      {!sessionsLoading && !sessionsError && !tracked && (
        <p
          role="alert"
          className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
        >
          This action requires a tracked browser session. Sign out and sign in
          again before retrying MFA recovery.
        </p>
      )}
      {errorMessage && (
        <p
          role="alert"
          className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
        >
          {errorMessage}
        </p>
      )}
    </>
  )
}

function LocalAdminVerification({
  draft,
  assuranceAvailable,
  loading,
  error,
  onDraftChange,
}: {
  draft: AdminMFAResetDraft
  assuranceAvailable: boolean
  loading: boolean
  error: unknown
  onDraftChange: (draft: AdminMFAResetDraft) => void
}) {
  return (
    <>
      <div>
        <label htmlFor="admin-mfa-reset-password" className="font-semibold">
          Your current password
        </label>
        <input
          id="admin-mfa-reset-password"
          type="password"
          autoComplete="current-password"
          value={draft.currentPassword}
          onChange={(event) =>
            onDraftChange({ ...draft, currentPassword: event.target.value })
          }
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
        />
      </div>
      {assuranceAvailable ? (
        <div>
          <label htmlFor="admin-mfa-reset-code" className="font-semibold">
            Your authenticator or recovery code
          </label>
          <input
            id="admin-mfa-reset-code"
            type="text"
            autoComplete="one-time-code"
            value={draft.code}
            onChange={(event) =>
              onDraftChange({ ...draft, code: event.target.value })
            }
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono dark:border-cyan-900/40 dark:bg-[#072019]"
          />
        </div>
      ) : !loading && !error ? (
        <p
          role="alert"
          className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
        >
          A local administrator must enable their own authenticator MFA before
          resetting another account.
        </p>
      ) : null}
    </>
  )
}

function OIDCAdminVerification({
  draft,
  verified,
  notice,
  pending,
  onStart,
}: {
  draft: AdminMFAResetDraft
  verified: boolean
  notice: OIDCCallbackNotice | null
  pending: boolean
  onStart: () => void
}) {
  const reasonMissing = draft.reason.trim().length < 3
  return (
    <div
      role={notice?.error ? 'alert' : 'status'}
      className={`rounded border px-3 py-2 ${
        verified
          ? 'border-green-300/60 bg-green-50 text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-200'
          : 'border-cyan/25 bg-cyan/5 text-slate dark:text-slate-200'
      }`}
    >
      <p className="font-semibold">Identity-provider verification</p>
      <p className="mt-1">
        {notice?.message ??
          'Complete a fresh SSO sign-in and the provider MFA required by your administrator policy.'}
      </p>
      {!verified && (
        <button
          type="button"
          className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
          onClick={onStart}
          disabled={pending || reasonMissing}
        >
          {pending ? 'Starting verification...' : 'Verify with SSO'}
        </button>
      )}
      {reasonMissing && (
        <p className="mt-1 text-xs">
          Enter the recovery reason before leaving for SSO verification.
        </p>
      )}
    </div>
  )
}

function RetryError({
  message,
  label,
  onRetry,
  disabled = false,
}: {
  message: string
  label: string
  onRetry: () => void
  disabled?: boolean
}) {
  return (
    <div
      role="alert"
      className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
    >
      <p>{message}</p>
      <button
        type="button"
        className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
        onClick={onRetry}
        disabled={disabled}
      >
        {label}
      </button>
    </div>
  )
}
