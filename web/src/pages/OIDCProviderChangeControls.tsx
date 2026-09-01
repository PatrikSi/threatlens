import { useEffect, useMemo, useState } from 'react'

import { ConfirmDialog } from '../components/ConfirmDialog'
import type {
  AuthSessionListResponse,
  CurrentAuthentication,
  MFAStatusResponse,
  OIDCProviderSettings,
} from '../types/api'
import { resolvePrivilegedSessionState } from './authSessionModel'
import {
  buildOIDCImpactReview,
  createOIDCDraft,
  diffOIDCSettings,
  overlappingOIDCChanges,
  type OIDCSettingsDraft,
} from './oidcSettingsDraft'
import type { OIDCCallbackNotice } from './oidcCallbackMessages'

const OIDC_CHANGE_LABELS: Partial<
  Record<keyof OIDCSettingsDraft, string>
> = {
  name: 'Provider name',
  enabled: 'Single sign-on status',
  publicBaseUrl: 'Application URL',
  roleClaim: 'Role claim name',
  roleMappings: 'Base-role mappings',
  defaultRole: 'Default base role',
  jitProvisioningEnabled: 'First-sign-in provisioning',
  autoApproveUsers: 'Automatic user approval',
  syncRolesOnLogin: 'Base-role synchronization',
}

function oidcChangeLabel(
  field: keyof OIDCSettingsDraft,
  fallback: string,
) {
  return OIDC_CHANGE_LABELS[field] ?? fallback
}

export type OIDCProviderRevisionConflict = {
  baseline: OIDCProviderSettings
  latest: OIDCProviderSettings | null
  saveError: string | null
  refreshError: string | null
}

export type LocalRecentAuthDraft = {
  currentPassword: string
  code: string
}

export function OIDCProviderSecurityGate({
  authentication,
  sessions,
  ownMfa,
  loading,
  fetching,
  error,
  ownMfaLoading,
  ownMfaError,
  reauthNotice,
  reauthPending,
  localDraft,
  localError,
  localPending,
  onRetry,
  onReauthenticate,
  onLocalDraftChange,
  onLocalReauthenticate,
}: {
  authentication?: CurrentAuthentication
  sessions?: AuthSessionListResponse
  ownMfa?: MFAStatusResponse
  loading: boolean
  fetching: boolean
  error: unknown
  ownMfaLoading: boolean
  ownMfaError: unknown
  reauthNotice: OIDCCallbackNotice | null
  reauthPending: boolean
  localDraft: LocalRecentAuthDraft
  localError: string | null
  localPending: boolean
  onRetry: () => void
  onReauthenticate: () => void
  onLocalDraftChange: (draft: LocalRecentAuthDraft) => void
  onLocalReauthenticate: () => void
}) {
  const sessionState = resolvePrivilegedSessionState(
    authentication,
    sessions,
    reauthNotice?.error === false,
  )
  if (loading) {
    return (
      <p role="status" className="text-sm text-slate dark:text-slate-300">
        Checking the current administrator session...
      </p>
    )
  }
  if (error) {
    return (
      <div
        role="alert"
        className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
      >
        <p>
          The current administrator session could not be verified. Provider
          changes are disabled.
        </p>
        <button
          type="button"
          className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
          onClick={onRetry}
          disabled={fetching}
        >
          {fetching ? 'Retrying...' : 'Retry session check'}
        </button>
      </div>
    )
  }
  if (!sessionState.tracked) {
    return (
      <div
        role="alert"
        className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
      >
        Provider changes require a tracked browser session. Sign out and sign in
        again before editing single sign-on settings.
      </div>
    )
  }
  if (sessionState.authMethod === 'local') {
    return (
      <LocalProviderVerification
        modernContract={sessionState.modernContract}
        verified={sessionState.recentAuthenticationValid}
        draft={localDraft}
        ownMfa={ownMfa}
        ownMfaLoading={ownMfaLoading}
        ownMfaError={ownMfaError}
        error={localError}
        pending={localPending}
        onDraftChange={onLocalDraftChange}
        onSubmit={onLocalReauthenticate}
        onRetry={onRetry}
      />
    )
  }
  return (
    <OIDCProviderVerification
      verified={sessionState.recentAuthenticationValid}
      notice={reauthNotice}
      pending={reauthPending}
      onStart={onReauthenticate}
    />
  )
}

function LocalProviderVerification({
  modernContract,
  verified,
  draft,
  ownMfa,
  ownMfaLoading,
  ownMfaError,
  error,
  pending,
  onDraftChange,
  onSubmit,
  onRetry,
}: {
  modernContract: boolean
  verified: boolean
  draft: LocalRecentAuthDraft
  ownMfa?: MFAStatusResponse
  ownMfaLoading: boolean
  ownMfaError: unknown
  error: string | null
  pending: boolean
  onDraftChange: (draft: LocalRecentAuthDraft) => void
  onSubmit: () => void
  onRetry: () => void
}) {
  if (!modernContract || verified) {
    return (
      <p
        role="status"
        className="rounded border border-green-300/60 bg-green-50 px-3 py-2 text-sm text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-200"
      >
        Current administrator session: recently verified local sign-in. Every
        change still requires an impact review.
      </p>
    )
  }

  const codeRequired = ownMfa?.enabled === true
  const submitDisabled =
    pending ||
    ownMfaLoading ||
    Boolean(ownMfaError) ||
    !draft.currentPassword ||
    (codeRequired && draft.code.trim().length < 6)
  return (
    <div className="rounded border border-amber-300/60 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
      <p className="font-semibold">Recent local verification required</p>
      <p className="mt-1">
        Confirm this session before editing or saving single sign-on policy.
        Recovery codes are not accepted here.
      </p>
      <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">
        <label className="min-w-0 font-semibold">
          Current password
          <input
            type="password"
            autoComplete="current-password"
            value={draft.currentPassword}
            onChange={(event) =>
              onDraftChange({
                ...draft,
                currentPassword: event.target.value,
              })
            }
            className="mt-1 w-full min-w-0 rounded border border-amber-500/40 bg-white px-3 py-2 text-ink dark:bg-[#072019] dark:text-white"
          />
        </label>
        {codeRequired && (
          <label className="min-w-0 font-semibold">
            Authenticator code
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={64}
              value={draft.code}
              onChange={(event) =>
                onDraftChange({ ...draft, code: event.target.value })
              }
              className="mt-1 w-full min-w-0 rounded border border-amber-500/40 bg-white px-3 py-2 font-mono text-ink dark:bg-[#072019] dark:text-white"
            />
          </label>
        )}
      </div>
      {ownMfaLoading && (
        <p role="status" className="mt-2">
          Checking local MFA requirements...
        </p>
      )}
      {Boolean(ownMfaError) && (
        <div role="alert" className="mt-2">
          <p>Local MFA requirements could not be loaded.</p>
          <button
            type="button"
            className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
            onClick={onRetry}
          >
            Retry verification check
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2">
          {error}
        </p>
      )}
      <button
        type="button"
        className="mt-3 min-h-11 w-full rounded bg-ink px-3 py-2 font-semibold text-white disabled:opacity-60 sm:w-auto dark:bg-cyan dark:text-[#053c2e]"
        disabled={submitDisabled}
        onClick={onSubmit}
      >
        {pending ? 'Verifying...' : 'Verify local session'}
      </button>
    </div>
  )
}

function OIDCProviderVerification({
  verified,
  notice,
  pending,
  onStart,
}: {
  verified: boolean
  notice: OIDCCallbackNotice | null
  pending: boolean
  onStart: () => void
}) {
  return (
    <div
      role={notice?.error ? 'alert' : 'status'}
      className={`rounded border px-3 py-2 text-sm ${
        verified
          ? 'border-green-300/60 bg-green-50 text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-200'
          : 'border-amber-300/60 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200'
      }`}
    >
      <p className="font-semibold">
        {verified ? 'SSO verification complete' : 'SSO verification required'}
      </p>
      <p className="mt-1">
        {notice?.message ??
          (verified
            ? 'Recent identity-provider authentication is valid for this security change.'
            : 'Verify with the identity provider before editing or saving authentication policy.')}
      </p>
      {!verified && (
        <button
          type="button"
          className="mt-2 min-h-11 w-full rounded border border-current px-3 py-2 font-semibold sm:w-auto"
          onClick={onStart}
          disabled={pending}
        >
          {pending ? 'Starting verification...' : 'Verify with SSO'}
        </button>
      )}
    </div>
  )
}

export function OIDCProviderConflictNotice({
  conflict,
  saveError,
  operatorDraft,
  refreshing,
  onRetryRefresh,
  onReload,
  onRebase,
}: {
  conflict: OIDCProviderRevisionConflict | null
  saveError?: string | null
  operatorDraft: OIDCSettingsDraft
  refreshing: boolean
  onRetryRefresh: () => void
  onReload: () => void
  onRebase: () => void
}) {
  if (!conflict) return null
  const baselineDraft = createOIDCDraft(conflict.baseline)
  const latestDraft = conflict.latest ? createOIDCDraft(conflict.latest) : null
  const serverChanges = latestDraft
    ? diffOIDCSettings(baselineDraft, latestDraft)
    : []
  const overlaps = latestDraft
    ? overlappingOIDCChanges(baselineDraft, operatorDraft, latestDraft)
    : []
  return (
    <div
      role="alert"
      className="mb-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
    >
      <p className="font-semibold">Settings changed on the server</p>
      <p className="mt-1">
        Save is blocked until you explicitly reload the server version or rebase
        your draft onto revision{' '}
        {conflict.latest?.config_revision ?? 'currently being loaded'}.
      </p>
      {(saveError || conflict.saveError) && (
        <p className="mt-2">{saveError || conflict.saveError}</p>
      )}
      {refreshing && (
        <p role="status" className="mt-2">
          Loading the latest server values...
        </p>
      )}
      {conflict.refreshError && (
        <div className="mt-2">
          <p>{conflict.refreshError}</p>
          <button
            type="button"
            className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
            onClick={onRetryRefresh}
            disabled={refreshing}
          >
            Retry latest settings
          </button>
        </div>
      )}
      {latestDraft && (
        <>
          <dl
            className="mt-3 space-y-2"
            aria-label="Changes made by the other administrator"
          >
            {serverChanges.length ? (
              serverChanges.map((change) => (
                <div
                  key={change.field}
                  className="border-l-2 border-amber-400/60 pl-3"
                >
                  <dt className="font-semibold">
                    {oidcChangeLabel(change.field, change.label)}
                  </dt>
                  <dd className="break-words">
                    Server: {change.previous} to {change.next}
                  </dd>
                </div>
              ))
            ) : (
              <div>
                The revision changed without a visible non-secret field change.
              </div>
            )}
          </dl>
          {conflict.baseline.has_client_secret !==
            conflict.latest?.has_client_secret && (
            <p className="mt-2 font-semibold">
              Stored client secret status changed:{' '}
              {conflict.latest?.has_client_secret
                ? 'a secret is now stored'
                : 'the stored secret was removed'}
              .
            </p>
          )}
          {overlaps.length > 0 && (
            <div className="mt-3 rounded border border-red-300/60 bg-red-50/80 px-3 py-2 text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
              <p className="font-semibold">
                Both administrators changed {overlaps.length} field
                {overlaps.length === 1 ? '' : 's'}
              </p>
              <ul className="mt-1 list-disc space-y-1 pl-4">
                {overlaps.map((change) => (
                  <li key={change.field}>
                    {oidcChangeLabel(change.field, change.label)}: server has{' '}
                    {change.previous}; your draft has{' '}
                    {change.next}
                  </li>
                ))}
              </ul>
              <p className="mt-2">
                Rebase keeps your values for these overlapping fields and adopts
                the server values everywhere you did not edit.
              </p>
            </div>
          )}
          <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <button
              type="button"
              className="min-h-11 rounded border border-current px-3 py-2 font-semibold"
              onClick={onReload}
            >
              Reload server values
            </button>
            <button
              type="button"
              className="min-h-11 rounded bg-ink px-3 py-2 font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
              onClick={onRebase}
            >
              Rebase my draft
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export function OIDCProviderActions({
  conflict,
  conflictSaveError,
  operatorDraft,
  refreshing,
  formError,
  saveFailure,
  savePending,
  saveSucceeded,
  hasUnsavedChanges,
  providerChangesAllowed,
  providerConfigured,
  testPending,
  testKeyCount,
  testFailure,
  onRetryRefresh,
  onReload,
  onRebase,
  onReview,
  onTest,
}: {
  conflict: OIDCProviderRevisionConflict | null
  conflictSaveError: string | null
  operatorDraft: OIDCSettingsDraft
  refreshing: boolean
  formError: string | null
  saveFailure: string | null
  savePending: boolean
  saveSucceeded: boolean
  hasUnsavedChanges: boolean
  providerChangesAllowed: boolean
  providerConfigured: boolean
  testPending: boolean
  testKeyCount: number | null
  testFailure: string | null
  onRetryRefresh: () => void
  onReload: () => void
  onRebase: () => void
  onReview: () => void
  onTest: () => void
}) {
  return (
    <section className="tl-surface rounded-xl p-3.5">
      <OIDCProviderConflictNotice
        conflict={conflict}
        saveError={conflictSaveError}
        operatorDraft={operatorDraft}
        refreshing={refreshing}
        onRetryRefresh={onRetryRefresh}
        onReload={onReload}
        onRebase={onRebase}
      />
      {(formError || saveFailure) && (
        <p
          id="oidc-form-error"
          role="alert"
          className="mb-3 text-sm text-red-600 dark:text-red-300"
        >
          {formError || saveFailure}
        </p>
      )}
      {saveSucceeded && (
        <p
          role="status"
          className="mb-3 text-sm text-green-700 dark:text-green-400"
        >
          Single sign-on settings saved.
        </p>
      )}
      {testKeyCount !== null && (
        <p
          role="status"
          className="mb-3 text-sm text-green-700 dark:text-green-400"
        >
          Discovery and {testKeyCount} signing key
          {testKeyCount === 1 ? '' : 's'} verified.
        </p>
      )}
      {testFailure && (
        <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-300">
          {testFailure}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded bg-ink px-4 py-2 font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
          disabled={
            savePending ||
            !hasUnsavedChanges ||
            Boolean(conflict) ||
            !providerChangesAllowed
          }
          onClick={onReview}
        >
          {savePending ? 'Saving...' : 'Review changes'}
        </button>
        <button
          type="button"
          className="rounded border border-slate/20 px-4 py-2 font-semibold dark:border-white/10"
          disabled={!providerConfigured || hasUnsavedChanges || testPending}
          onClick={onTest}
        >
          {testPending ? 'Testing connection...' : 'Test connection'}
        </button>
      </div>
      {hasUnsavedChanges && (
        <p className="mt-2 text-xs text-slate dark:text-slate-300">
          Review and save draft changes before testing the persisted discovery
          document and signing keys.
        </p>
      )}
    </section>
  )
}

export function OIDCProviderSaveDialog({
  open,
  baseline,
  draft,
  pending,
  onCancel,
  onConfirm,
}: {
  open: boolean
  baseline: OIDCSettingsDraft
  draft: OIDCSettingsDraft
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const review = useMemo(
    () => buildOIDCImpactReview(baseline, draft),
    [baseline, draft],
  )
  const [acknowledged, setAcknowledged] = useState(false)
  useEffect(() => {
    if (!open) setAcknowledged(false)
  }, [open])
  return (
    <ConfirmDialog
      open={open}
      title="Save single sign-on changes?"
      description="Review the sign-in, provisioning, and access consequences before saving."
      confirmLabel="Save single sign-on changes"
      confirmTone={review.requiresAcknowledgement ? 'danger' : 'primary'}
      confirmDisabled={
        !review.changes.length ||
        (review.requiresAcknowledgement && !acknowledged)
      }
      isConfirming={pending}
      onCancel={() => {
        setAcknowledged(false)
        onCancel()
      }}
      onConfirm={() => {
        setAcknowledged(false)
        onConfirm()
      }}
    >
      <div className="space-y-3 text-sm">
        <dl className="space-y-2" aria-label="Single sign-on changes">
          {review.changes.map((change) => (
            <div
              key={change.field}
              className="border-l-2 border-slate/20 pl-3 dark:border-white/20"
            >
              <dt className="font-semibold">
                {oidcChangeLabel(change.field, change.label)}
              </dt>
              <dd className="break-words text-slate dark:text-white/75">
                {change.previous} to {change.next}
              </dd>
            </div>
          ))}
        </dl>
        {review.warnings.length > 0 && (
          <div className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
            <p className="font-semibold">Security impact</p>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {review.warnings.map((warning) => (
                <li key={warning.message}>{warning.message}</li>
              ))}
            </ul>
          </div>
        )}
        {review.requiresAcknowledgement && (
          <label className="flex items-start gap-2 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
            <input
              type="checkbox"
              className="mt-0.5 h-5 w-5 shrink-0 sm:h-4 sm:w-4"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            <span>I reviewed the lockout and administrator-access risks.</span>
          </label>
        )}
      </div>
    </ConfirmDialog>
  )
}
