import { FormEvent, useEffect, useRef, useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useAuth } from '../components/AuthContext'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type {
  AuthSession,
  AuthSessionListResponse,
  MFADisableResponse,
  MFAEnrollmentCancelResponse,
  MFAEnrollmentResponse,
  MFARecoveryCodesResponse,
  MFAStatusResponse,
  RecentAuthenticationResponse,
  SessionBulkRevocationResponse,
  SessionRevocationResponse,
} from '../types/identity'
import { formatDateTime } from '../utils/datetime'
import {
  describeSessionClient,
  downloadRecoveryCodes,
  formatSessionRevocationResult,
  isSessionReauthenticationRequired,
  sessionStatus,
  type SessionReauthenticationDraft,
  type SessionRevocationAction,
} from './accountSecurityModel'
import { AccountSessionsWorkspace } from './AccountSessionsWorkspace'
import {
  DialogError,
  RecoveryCodesDialog,
  SecretValue,
  SensitiveActionDialog,
  type SensitiveAction,
  type SensitiveDraft,
} from './AccountSecurityControls'
import { disableWhen } from './accountSecurityUtils'
import {
  resolveOIDCReauthNotice,
  type OIDCCallbackNotice,
} from './oidcCallbackMessages'
import {
  beginOIDCReauthentication,
  readOIDCReauthNavigationState,
} from './oidcReauthentication'
import { SessionReauthenticationControls } from './SessionReauthenticationControls'

const SECURITY_QUERY_KEY = ['auth', 'security'] as const
const MFA_ENROLLMENT_MUTATION_KEY = [
  ...SECURITY_QUERY_KEY,
  'mfa',
  'enroll',
] as const
const MFA_ENROLLMENT_CANCEL_MUTATION_KEY = [
  ...SECURITY_QUERY_KEY,
  'mfa',
  'enrollment',
  'cancel',
] as const
const MFA_CONFIRMATION_MUTATION_KEY = [
  ...SECURITY_QUERY_KEY,
  'mfa',
  'confirm',
] as const
const MFA_SENSITIVE_MUTATION_KEY = [
  ...SECURITY_QUERY_KEY,
  'mfa',
  'sensitive',
] as const

type SecurityNotice = {
  tone: 'success' | 'error'
  message: string
}

function successNotice(message: string): SecurityNotice {
  return { tone: 'success', message }
}

function errorNotice(message: string): SecurityNotice {
  return { tone: 'error', message }
}

function SecurityStatusNotice({ notice }: { notice: SecurityNotice }) {
  const isError = notice.tone === 'error'
  return (
    <p
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
      aria-atomic="true"
      className={`rounded border px-3 py-2 text-sm ${
        isError
          ? 'border-red-300/60 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200'
          : 'border-green-300/60 bg-green-50 text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-200'
      }`}
    >
      {notice.message}
    </p>
  )
}

const EMPTY_SENSITIVE_DRAFT: SensitiveDraft = { currentPassword: '', code: '' }
const EMPTY_SESSION_REAUTH_DRAFT: SessionReauthenticationDraft = {
  currentPassword: '',
  code: '',
}

function forgetSettledMutationResult(
  queryClient: QueryClient,
  mutationKey: readonly unknown[],
  reset: () => void,
) {
  reset()
  const mutationCache = queryClient.getMutationCache()
  mutationCache.findAll({ mutationKey, exact: true }).forEach((mutation) => {
    if (mutation.state.status !== 'pending') mutationCache.remove(mutation)
  })
}

export function AccountSecuritySection({
  onDraftWarningChange,
}: {
  onDraftWarningChange?: (warning: string | null) => void
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { markLoggedOut } = useAuth()
  const meQuery = useCurrentUser()
  const [enrollmentPassword, setEnrollmentPassword] = useState('')
  const [enrollment, setEnrollment] = useState<MFAEnrollmentResponse | null>(
    null,
  )
  const [confirmationCode, setConfirmationCode] = useState('')
  const [recoveryCodes, setRecoveryCodes] =
    useState<MFARecoveryCodesResponse | null>(null)
  const [sensitiveAction, setSensitiveAction] =
    useState<SensitiveAction | null>(null)
  const [sensitiveDraft, setSensitiveDraft] = useState<SensitiveDraft>(
    EMPTY_SENSITIVE_DRAFT,
  )
  const [sessionToRevoke, setSessionToRevoke] = useState<AuthSession | null>(
    null,
  )
  const [revokeOthersOpen, setRevokeOthersOpen] = useState(false)
  const [sessionReauthAction, setSessionReauthAction] =
    useState<SessionRevocationAction | null>(null)
  const [sessionReauthDraft, setSessionReauthDraft] = useState(
    EMPTY_SESSION_REAUTH_DRAFT,
  )
  const [notice, setNotice] = useState<SecurityNotice | null>(null)
  const reauthContinuationHandledRef = useRef(false)
  const hasSensitiveDraft = Boolean(
    enrollment ||
    recoveryCodes ||
    enrollmentPassword ||
    confirmationCode ||
    sensitiveDraft.currentPassword ||
    sensitiveDraft.code,
  )
  const draftWarning = !hasSensitiveDraft
    ? null
    : recoveryCodes
      ? 'Recovery codes are only shown once. Leave before confirming that they are stored?'
      : 'Leave without completing the security change?'

  useEffect(() => {
    onDraftWarningChange?.(draftWarning)
    return () => onDraftWarningChange?.(null)
  }, [draftWarning, onDraftWarningChange])

  const mfaQuery = useQuery({
    queryKey: [...SECURITY_QUERY_KEY, 'mfa'],
    queryFn: () => apiFetch<MFAStatusResponse>('/auth/security/mfa'),
  })
  const sessionsQuery = useQuery({
    queryKey: [...SECURITY_QUERY_KEY, 'sessions'],
    queryFn: () => apiFetch<AuthSessionListResponse>('/auth/security/sessions'),
  })
  const sessionReauthNavigation = readOIDCReauthNavigationState(
    location.state,
    'session_revocation',
  )
  const sessionAuthMethod =
    meQuery.data?.authentication?.session_auth_method ??
    sessionsQuery.data?.sessions?.find((session) => session.current)?.auth_method

  const refreshSecurity = async () => {
    await queryClient.invalidateQueries({ queryKey: SECURITY_QUERY_KEY })
  }

  const enrollmentMutation = useMutation({
    mutationKey: MFA_ENROLLMENT_MUTATION_KEY,
    gcTime: 0,
    mutationFn: () =>
      apiFetch<MFAEnrollmentResponse>('/auth/security/mfa/enroll', {
        method: 'POST',
        body: JSON.stringify({ current_password: enrollmentPassword }),
      }),
    onSuccess: (result) => {
      setEnrollmentPassword('')
      setConfirmationCode('')
      setEnrollment(result)
      setNotice(
        successNotice(
          'Authenticator enrollment started. Add the account to your authenticator, then verify it.',
        ),
      )
    },
  })
  const confirmationMutation = useMutation({
    mutationKey: MFA_CONFIRMATION_MUTATION_KEY,
    gcTime: 0,
    mutationFn: () =>
      apiFetch<MFARecoveryCodesResponse>('/auth/security/mfa/confirm', {
        method: 'POST',
        body: JSON.stringify({ code: confirmationCode.trim() }),
      }),
    onSuccess: async (result) => {
      setConfirmationCode('')
      setEnrollment(null)
      setRecoveryCodes(result)
      forgetSettledMutationResult(
        queryClient,
        MFA_ENROLLMENT_MUTATION_KEY,
        enrollmentMutation.reset,
      )
      setNotice(
        successNotice(
          'Multi-factor authentication is enabled. Other browser sessions were revoked.',
        ),
      )
      await refreshSecurity()
    },
  })
  const enrollmentCancellationMutation = useMutation({
    mutationKey: MFA_ENROLLMENT_CANCEL_MUTATION_KEY,
    gcTime: 0,
    mutationFn: () =>
      apiFetch<MFAEnrollmentCancelResponse>('/auth/security/mfa/enrollment', {
        method: 'DELETE',
      }),
    onSuccess: (result) => {
      setEnrollment(null)
      setConfirmationCode('')
      forgetSettledMutationResult(
        queryClient,
        MFA_ENROLLMENT_MUTATION_KEY,
        enrollmentMutation.reset,
      )
      forgetSettledMutationResult(
        queryClient,
        MFA_CONFIRMATION_MUTATION_KEY,
        confirmationMutation.reset,
      )
      setNotice(
        successNotice(
          result.cancelled
            ? 'Authenticator setup cancelled.'
            : 'Authenticator setup was already inactive.',
        ),
      )
    },
  })
  const sensitiveMutation = useMutation({
    mutationKey: MFA_SENSITIVE_MUTATION_KEY,
    gcTime: 0,
    mutationFn: async (action: SensitiveAction) => {
      const options = {
        body: JSON.stringify({
          current_password: sensitiveDraft.currentPassword,
          code: sensitiveDraft.code.trim(),
        }),
      }
      if (action === 'regenerate') {
        return apiFetch<MFARecoveryCodesResponse>(
          '/auth/security/mfa/recovery-codes',
          {
            ...options,
            method: 'POST',
          },
        )
      }
      return apiFetch<MFADisableResponse>('/auth/security/mfa', {
        ...options,
        method: 'DELETE',
      })
    },
    onSuccess: async (result, action) => {
      setSensitiveAction(null)
      setSensitiveDraft(EMPTY_SENSITIVE_DRAFT)
      if (action === 'regenerate' && 'recovery_codes' in result) {
        setRecoveryCodes(result)
        setNotice(
          successNotice(
            'New recovery codes generated. Previous recovery codes no longer work.',
          ),
        )
      } else {
        setNotice(
          successNotice(
            'Multi-factor authentication disabled. Other browser sessions were revoked.',
          ),
        )
      }
      await refreshSecurity()
    },
  })
  const revokeSessionMutation = useMutation({
    mutationFn: (session: AuthSession) =>
      apiFetch<SessionRevocationResponse>(
        `/auth/security/sessions/${session.id}`,
        {
          method: 'DELETE',
        },
      ),
    onSuccess: async (result) => {
      setSessionToRevoke(null)
      if (result.current_session_revoked) {
        markLoggedOut()
        navigate('/login', {
          replace: true,
          state: {
            authMessage:
              'This browser session was revoked. Sign in to continue.',
          },
        })
        return
      }
      setNotice(
        successNotice(
          result.revoked
            ? formatSessionRevocationResult(result)
            : 'That browser session was already inactive.',
        ),
      )
      await refreshSecurity()
    },
    onError: (error, session) => {
      if (isSessionReauthenticationRequired(error)) {
        setSessionReauthAction({ kind: 'single', sessionId: session.id })
      }
    },
  })
  const revokeOthersMutation = useMutation({
    mutationFn: () =>
      apiFetch<SessionBulkRevocationResponse>(
        '/auth/security/sessions/revoke-others',
        {
          method: 'POST',
        },
      ),
    onSuccess: async ({ revoked_count: revokedCount }) => {
      setRevokeOthersOpen(false)
      setNotice(
        successNotice(
          revokedCount === 0
            ? 'No other active browser sessions were found.'
            : `${revokedCount} other browser session${revokedCount === 1 ? '' : 's'} revoked.`,
        ),
      )
      await refreshSecurity()
    },
    onError: (error) => {
      if (isSessionReauthenticationRequired(error)) {
        setSessionReauthAction({ kind: 'others' })
      }
    },
  })
  const localSessionReauthentication = useMutation({
    mutationFn: () =>
      apiFetch<RecentAuthenticationResponse>('/auth/security/reauthenticate', {
        method: 'POST',
        body: JSON.stringify({
          current_password: sessionReauthDraft.currentPassword,
          ...(sessionReauthDraft.code.trim()
            ? { code: sessionReauthDraft.code.trim() }
            : {}),
        }),
      }),
    onSuccess: async () => {
      setSessionReauthDraft(EMPTY_SESSION_REAUTH_DRAFT)
      setSessionReauthAction(null)
      revokeSessionMutation.reset()
      revokeOthersMutation.reset()
      setNotice(
        successNotice(
          'This browser session was verified. Review the revocation details and confirm the action again.',
        ),
      )
      await Promise.all([meQuery.refetch(), refreshSecurity()])
    },
  })
  const oidcSessionReauthentication = useMutation({
    mutationFn: (action: SessionRevocationAction) =>
      beginOIDCReauthentication({
        returnPath: '/settings/account',
        purpose: 'session_revocation',
        context: {
          sessionAction: action.kind,
          ...(action.kind === 'single' ? { sessionId: action.sessionId } : {}),
        },
      }),
  })

  useEffect(() => {
    if (
      reauthContinuationHandledRef.current ||
      !sessionReauthNavigation ||
      !sessionsQuery.data
    ) {
      return
    }
    const context = sessionReauthNavigation.context
    const action: SessionRevocationAction | null =
      context?.sessionAction === 'others'
        ? { kind: 'others' }
        : context?.sessionAction === 'single' && context.sessionId
          ? { kind: 'single', sessionId: context.sessionId }
          : null
    reauthContinuationHandledRef.current = true
    navigate(`${location.pathname}${location.search}`, {
      replace: true,
      state: null,
    })
    const callbackNotice: OIDCCallbackNotice = resolveOIDCReauthNotice(
      sessionReauthNavigation.result,
    )
    if (!action) {
      setNotice(
        errorNotice(
          `${callbackNotice.message} The original session-revocation target could not be restored; select it again.`,
        ),
      )
      return
    }
    if (action.kind === 'others') {
      setRevokeOthersOpen(true)
    } else {
      const target = sessionsQuery.data.sessions.find(
        (session) => session.id === action.sessionId,
      )
      if (!target || !['active', 'current'].includes(sessionStatus(target))) {
        setNotice(
          errorNotice(
            `${callbackNotice.message} The selected browser session is no longer active.`,
          ),
        )
        return
      }
      setSessionToRevoke(target)
    }
    setSessionReauthAction(callbackNotice.error ? action : null)
    setNotice(
      callbackNotice.error
        ? errorNotice(callbackNotice.message)
        : successNotice(
            `${callbackNotice.message} Review the revocation details and confirm the action again.`,
          ),
    )
  }, [
    location.pathname,
    location.search,
    navigate,
    sessionReauthNavigation,
    sessionsQuery.data,
  ])

  const dismissRecoveryCodes = () => {
    setRecoveryCodes(null)
    forgetSettledMutationResult(
      queryClient,
      MFA_CONFIRMATION_MUTATION_KEY,
      confirmationMutation.reset,
    )
    forgetSettledMutationResult(
      queryClient,
      MFA_SENSITIVE_MUTATION_KEY,
      sensitiveMutation.reset,
    )
    setNotice(
      successNotice(
        'Recovery codes marked as stored. They cannot be displayed again.',
      ),
    )
  }

  const singleSessionReauthenticationRequired = Boolean(
    sessionReauthAction?.kind === 'single' &&
      sessionReauthAction.sessionId === sessionToRevoke?.id,
  )
  const otherSessionsReauthenticationRequired =
    sessionReauthAction?.kind === 'others'

  return (
    <section
      className="space-y-4"
      aria-labelledby="account-security-heading"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="account-security-heading" className="font-display text-xl">
            Account security
          </h2>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">
            Protect local sign-in and review browsers that can access this
            account.
          </p>
        </div>
        <button
          type="button"
          className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
          onClick={() => void refreshSecurity()}
          disabled={mfaQuery.isFetching || sessionsQuery.isFetching}
        >
          {mfaQuery.isFetching || sessionsQuery.isFetching
            ? 'Refreshing...'
            : 'Refresh security status'}
        </button>
      </div>

      {notice && <SecurityStatusNotice notice={notice} />}

      <div className="grid gap-4 lg:grid-cols-2">
        <MFAWorkspace
          status={mfaQuery.data}
          isLoading={mfaQuery.isLoading}
          loadError={mfaQuery.error}
          actionsDisabled={mfaQuery.isError}
          enrollment={enrollment}
          enrollmentPassword={enrollmentPassword}
          confirmationCode={confirmationCode}
          enrollmentMutation={enrollmentMutation}
          confirmationMutation={confirmationMutation}
          enrollmentCancellationMutation={enrollmentCancellationMutation}
          onEnrollmentPasswordChange={setEnrollmentPassword}
          onConfirmationCodeChange={setConfirmationCode}
          onCancelEnrollment={() => enrollmentCancellationMutation.mutate()}
          onSensitiveAction={setSensitiveAction}
        />
        <AccountSessionsWorkspace
          data={sessionsQuery.data}
          isLoading={sessionsQuery.isLoading}
          loadError={sessionsQuery.error}
          actionsDisabled={sessionsQuery.isError}
          onRevoke={setSessionToRevoke}
          onRevokeOthers={() => setRevokeOthersOpen(true)}
        />
      </div>

      <SensitiveActionDialog
        action={sensitiveAction}
        draft={sensitiveDraft}
        error={sensitiveMutation.error}
        isPending={sensitiveMutation.isPending}
        actionsDisabled={mfaQuery.isError}
        onDraftChange={setSensitiveDraft}
        onConfirm={() =>
          sensitiveAction && sensitiveMutation.mutate(sensitiveAction)
        }
        onCancel={() => {
          if (!sensitiveMutation.isPending) {
            setSensitiveAction(null)
            setSensitiveDraft(EMPTY_SENSITIVE_DRAFT)
            sensitiveMutation.reset()
          }
        }}
      />
      <RecoveryCodesDialog
        data={recoveryCodes}
        onDownload={downloadRecoveryCodes}
        onDone={dismissRecoveryCodes}
      />
      <ConfirmDialog
        open={Boolean(sessionToRevoke)}
        title={
          sessionToRevoke?.current
            ? 'Revoke this browser session?'
            : 'Revoke browser session?'
        }
        description={
          sessionToRevoke?.current
            ? 'ThreatLens will sign this browser out immediately. Unsaved work in other tabs may be lost.'
            : `Revoke ${sessionToRevoke ? describeSessionClient(sessionToRevoke) : 'this session'}? Only the selected browser session will be signed out. This browser, other sessions, and API tokens remain active. Recent authentication may be required.`
        }
        confirmLabel={
          sessionToRevoke?.current
            ? 'Revoke and sign out'
            : 'Revoke browser access'
        }
        isConfirming={revokeSessionMutation.isPending}
        confirmDisabled={disableWhen(
          singleSessionReauthenticationRequired,
          sessionsQuery.isError,
        )}
        onConfirm={() =>
          sessionToRevoke && revokeSessionMutation.mutate(sessionToRevoke)
        }
        onCancel={() => {
          setSessionToRevoke(null)
          setSessionReauthAction(null)
          setSessionReauthDraft(EMPTY_SESSION_REAUTH_DRAFT)
          revokeSessionMutation.reset()
          localSessionReauthentication.reset()
          oidcSessionReauthentication.reset()
        }}
      >
        <StaleSessionDialogNotice stale={sessionsQuery.isError} />
        {revokeSessionMutation.isError && (
          <DialogError
            error={revokeSessionMutation.error}
            fallback="The browser session could not be revoked"
          />
        )}
        <SessionReauthenticationControls
          action={
            singleSessionReauthenticationRequired ? sessionReauthAction : null
          }
          authMethod={sessionAuthMethod}
          mfaStatus={mfaQuery.data}
          mfaLoading={mfaQuery.isLoading}
          mfaError={mfaQuery.error}
          draft={sessionReauthDraft}
          localError={localSessionReauthentication.error}
          localPending={localSessionReauthentication.isPending}
          oidcError={oidcSessionReauthentication.error}
          oidcPending={oidcSessionReauthentication.isPending}
          onDraftChange={setSessionReauthDraft}
          onLocalVerify={() => localSessionReauthentication.mutate()}
          onOIDCVerify={() =>
            sessionReauthAction &&
            oidcSessionReauthentication.mutate(sessionReauthAction)
          }
          onRetryMfa={() => void mfaQuery.refetch()}
        />
      </ConfirmDialog>
      <ConfirmDialog
        open={revokeOthersOpen}
        title="Revoke all other browser sessions?"
        description="Every active browser session except this one will be revoked. API tokens are not affected."
        confirmLabel="Revoke other sessions"
        isConfirming={revokeOthersMutation.isPending}
        confirmDisabled={disableWhen(
          otherSessionsReauthenticationRequired,
          sessionsQuery.isError,
        )}
        onConfirm={() => revokeOthersMutation.mutate()}
        onCancel={() => {
          setRevokeOthersOpen(false)
          setSessionReauthAction(null)
          setSessionReauthDraft(EMPTY_SESSION_REAUTH_DRAFT)
          revokeOthersMutation.reset()
          localSessionReauthentication.reset()
          oidcSessionReauthentication.reset()
        }}
      >
        <StaleSessionDialogNotice stale={sessionsQuery.isError} />
        {revokeOthersMutation.isError && (
          <DialogError
            error={revokeOthersMutation.error}
            fallback="Other browser sessions could not be revoked"
          />
        )}
        <SessionReauthenticationControls
          action={sessionReauthAction?.kind === 'others' ? sessionReauthAction : null}
          authMethod={sessionAuthMethod}
          mfaStatus={mfaQuery.data}
          mfaLoading={mfaQuery.isLoading}
          mfaError={mfaQuery.error}
          draft={sessionReauthDraft}
          localError={localSessionReauthentication.error}
          localPending={localSessionReauthentication.isPending}
          oidcError={oidcSessionReauthentication.error}
          oidcPending={oidcSessionReauthentication.isPending}
          onDraftChange={setSessionReauthDraft}
          onLocalVerify={() => localSessionReauthentication.mutate()}
          onOIDCVerify={() =>
            sessionReauthAction &&
            oidcSessionReauthentication.mutate(sessionReauthAction)
          }
          onRetryMfa={() => void mfaQuery.refetch()}
        />
      </ConfirmDialog>
    </section>
  )
}

function StaleSessionDialogNotice({ stale }: { stale: boolean }) {
  if (!stale) return null
  return (
    <p
      role="alert"
      aria-live="assertive"
      className="rounded border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/25 dark:text-amber-200"
    >
      Session revocation is disabled because the latest session list could not be loaded. Close this
      dialog, refresh security status, then review the current sessions before trying again.
    </p>
  )
}

type MutationState = {
  isPending: boolean
  isError: boolean
  error: unknown
  mutate: () => void
}

function MFAWorkspace({
  status,
  isLoading,
  loadError,
  actionsDisabled,
  enrollment,
  enrollmentPassword,
  confirmationCode,
  enrollmentMutation,
  confirmationMutation,
  enrollmentCancellationMutation,
  onEnrollmentPasswordChange,
  onConfirmationCodeChange,
  onCancelEnrollment,
  onSensitiveAction,
}: {
  status?: MFAStatusResponse
  isLoading: boolean
  loadError: unknown
  actionsDisabled: boolean
  enrollment: MFAEnrollmentResponse | null
  enrollmentPassword: string
  confirmationCode: string
  enrollmentMutation: MutationState
  confirmationMutation: MutationState
  enrollmentCancellationMutation: MutationState
  onEnrollmentPasswordChange: (value: string) => void
  onConfirmationCodeChange: (value: string) => void
  onCancelEnrollment: () => void
  onSensitiveAction: (action: SensitiveAction) => void
}) {
  return (
    <div className="tl-surface rounded-xl p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold">Multi-factor authentication</h3>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">
            Require a second factor after a local password.
          </p>
        </div>
        {status && (
          <span
            className={`rounded px-2 py-1 text-xs font-semibold ${status.enabled ? 'bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-200' : 'bg-slate/10 text-slate dark:bg-white/10 dark:text-slate-200'}`}
          >
            {status.enabled
              ? 'Enabled'
              : status.managed_by === 'identity_provider'
                ? 'SSO managed'
                : 'Not enabled'}
          </span>
        )}
      </div>

      {isLoading && (
        <p className="mt-3 text-sm text-slate dark:text-slate-300">
          Loading MFA status...
        </p>
      )}
      {Boolean(loadError) && (
        <DialogError
          error={loadError}
          fallback="MFA status could not be loaded"
        />
      )}
      {actionsDisabled && (
        <p
          id="mfa-actions-stale"
          role="status"
          className="mt-2 text-xs font-semibold text-amber-800 dark:text-amber-200"
        >
          Security actions are disabled until the current MFA status can be
          loaded.
        </p>
      )}
      {status?.managed_by === 'identity_provider' && (
        <div className="mt-3 rounded border border-cyan/25 bg-cyan/5 px-3 py-3 text-sm">
          <p className="font-semibold">Managed by your identity provider</p>
          <p className="mt-1 text-slate dark:text-slate-300">
            ThreatLens does not add a second local MFA prompt to SSO sign-in.
            Configure MFA and recovery methods in your identity provider.
          </p>
        </div>
      )}
      {status?.local_mfa_available && !status.enabled && !enrollment && (
        <form
          className="mt-4"
          onSubmit={(event: FormEvent) => {
            event.preventDefault()
            if (enrollmentPassword) enrollmentMutation.mutate()
          }}
        >
          <label
            htmlFor="mfa-enrollment-password"
            className="text-sm font-semibold"
          >
            Current password
          </label>
          <input
            id="mfa-enrollment-password"
            type="password"
            autoComplete="current-password"
            value={enrollmentPassword}
            onChange={(event) => onEnrollmentPasswordChange(event.target.value)}
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            required
            disabled={actionsDisabled}
            aria-describedby={actionsDisabled ? 'mfa-actions-stale' : undefined}
          />
          {enrollmentMutation.isError && (
            <DialogError
              error={enrollmentMutation.error}
              fallback="Authenticator enrollment could not be started"
            />
          )}
          <button
            type="submit"
            className="mt-3 min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
            disabled={disableWhen(
              actionsDisabled,
              !enrollmentPassword,
              enrollmentMutation.isPending,
            )}
          >
            {enrollmentMutation.isPending
              ? 'Starting...'
              : 'Set up authenticator'}
          </button>
        </form>
      )}
      {status?.local_mfa_available && !status.enabled && enrollment && (
        <div className="mt-4 space-y-3">
          <div>
            <p className="text-sm font-semibold">
              1. Add this account to your authenticator
            </p>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              Enter the secret manually, or open the provisioning URI in a
              compatible authenticator.
            </p>
          </div>
          <SecretValue label="Secret" value={enrollment.secret} />
          <SecretValue
            label="Provisioning URI"
            value={enrollment.provisioning_uri}
          />
          <form
            onSubmit={(event: FormEvent) => {
              event.preventDefault()
              if (confirmationCode.trim()) confirmationMutation.mutate()
            }}
          >
            <label
              htmlFor="mfa-confirmation-code"
              className="text-sm font-semibold"
            >
              2. Verify a current code
            </label>
            <input
              id="mfa-confirmation-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={confirmationCode}
              onChange={(event) => onConfirmationCodeChange(event.target.value)}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono dark:border-cyan-900/40 dark:bg-[#072019]"
              required
              disabled={actionsDisabled}
              aria-describedby={actionsDisabled ? 'mfa-actions-stale' : undefined}
            />
            {confirmationMutation.isError && (
              <DialogError
                error={confirmationMutation.error}
                fallback="The authenticator code could not be confirmed"
              />
            )}
            {enrollmentCancellationMutation.isError && (
              <DialogError
                error={enrollmentCancellationMutation.error}
                fallback="Authenticator setup could not be cancelled"
              />
            )}
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <button
                type="submit"
                className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
                disabled={disableWhen(
                  !confirmationCode.trim(),
                  confirmationMutation.isPending,
                  actionsDisabled,
                )}
              >
                {confirmationMutation.isPending ? 'Verifying...' : 'Enable MFA'}
              </button>
              <button
                type="button"
                className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
                onClick={onCancelEnrollment}
                disabled={disableWhen(
                  confirmationMutation.isPending,
                  enrollmentCancellationMutation.isPending,
                  actionsDisabled,
                )}
              >
                {enrollmentCancellationMutation.isPending
                  ? 'Cancelling...'
                  : 'Cancel setup'}
              </button>
            </div>
          </form>
        </div>
      )}
      {status?.enabled && (
        <div className="mt-4 space-y-3 text-sm">
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-2">
            <dt className="text-slate dark:text-slate-300">Enabled</dt>
            <dd className="text-right">
              {formatDateTime(status.confirmed_at)}
            </dd>
            <dt className="text-slate dark:text-slate-300">
              Recovery codes remaining
            </dt>
            <dd className="text-right font-semibold">
              {status.recovery_codes_remaining}
            </dd>
          </dl>
          {status.recovery_codes_remaining <= 2 && (
            <p className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              Recovery codes are running low. Generate a new set before you need
              account recovery.
            </p>
          )}
          <div className="flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              className="min-h-11 rounded border border-slate/20 px-3 py-2 font-semibold dark:border-cyan-900/40"
              onClick={() => onSensitiveAction('regenerate')}
              disabled={actionsDisabled}
              aria-describedby={actionsDisabled ? 'mfa-actions-stale' : undefined}
            >
              Generate new recovery codes
            </button>
            <button
              type="button"
              className="tl-button-danger min-h-11 rounded px-3 py-2 font-semibold"
              onClick={() => onSensitiveAction('disable')}
              disabled={actionsDisabled}
              aria-describedby={actionsDisabled ? 'mfa-actions-stale' : undefined}
            >
              Disable MFA
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
