import { FormEvent, useEffect, useRef, useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useAuth } from '../components/AuthContext'
import { DialogSurface } from '../components/ConfirmDialog'
import { SettingsPageHeader } from '../components/SettingsPageHeader'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { formatDateTime } from '../utils/datetime'
import { formatSettingsRoleLabel } from '../workspace/modulePresentation'
import type {
  MFAStatusResponse,
  OIDCAccountStatus,
  OIDCLinkStartRequest,
  OIDCStartResponse,
  OIDCUnlinkRequest,
  OIDCUnlinkResponse,
  PasswordChangeResponse,
} from '../types/api'
import { PasswordManagementSection } from './AccountPasswordManagement'
import { AccountSecuritySection } from './AccountSecuritySection'
import {
  resolveOIDCLinkNotice,
  resolveOIDCReauthNotice,
  type OIDCCallbackNotice,
} from './oidcCallbackMessages'
import { consumeOIDCReauthContinuation } from './oidcReauthentication'

const MFA_STATUS_QUERY_KEY = ['auth', 'security', 'mfa'] as const
const OIDC_LINK_MUTATION_KEY = ['auth', 'oidc', 'link'] as const
const OIDC_UNLINK_MUTATION_KEY = ['auth', 'oidc', 'unlink'] as const

function forgetSettledMutation(
  queryClient: QueryClient,
  mutationKey: readonly unknown[],
  reset: () => void,
) {
  reset()
  removeSettledMutations(queryClient, mutationKey)
}

function removeSettledMutations(
  queryClient: QueryClient,
  mutationKey: readonly unknown[],
) {
  const mutationCache = queryClient.getMutationCache()
  mutationCache.findAll({ mutationKey, exact: true }).forEach((mutation) => {
    if (mutation.state.status !== 'pending') mutationCache.remove(mutation)
  })
}

type OIDCLinkDraft = {
  currentPassword: string
  code: string
}

type OIDCLinkValidationErrors = Partial<
  Record<keyof OIDCLinkDraft | 'form', string>
>
type OIDCLinkController = ReturnType<typeof useOIDCLinkController>
type OIDCUnlinkController = ReturnType<typeof useOIDCUnlinkController>

const EMPTY_OIDC_LINK_DRAFT: OIDCLinkDraft = {
  currentPassword: '',
  code: '',
}

export function AccountPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { markLoggedOut } = useAuth()
  const meQuery = useCurrentUser()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [passwordFormError, setPasswordFormError] = useState('')
  const [passwordNotice, setPasswordNotice] = useState('')
  const [securityDraftWarning, setSecurityDraftWarning] = useState<
    string | null
  >(null)
  const [reauthContinuation] = useState(() =>
    consumeOIDCReauthContinuation(
      new URLSearchParams(location.search).get('oidc_reauth'),
    ),
  )
  const oidcLink = useOIDCLinkController()
  const oidcUnlink = useOIDCUnlinkController()
  const oidcStatusQuery = useQuery({
    queryKey: ['auth', 'oidc', 'account'],
    queryFn: () => apiFetch<OIDCAccountStatus>('/auth/oidc/account'),
  })
  const ssoProvisioned = meQuery.data?.provisioning_source === 'oidc'
  const passwordLoginEnabled =
    !ssoProvisioned && meQuery.data?.password_login_enabled !== false
  const passwordDraftDirty =
    currentPassword.length > 0 || newPassword.length > 0
  const confirmDiscardPasswordDraft = useUnsavedChangesWarning(
    passwordDraftDirty ||
      oidcLink.isDraftDirty ||
      oidcUnlink.isDraftDirty ||
      Boolean(securityDraftWarning),
    securityDraftWarning ||
      (oidcLink.isDraftDirty || oidcUnlink.isDraftDirty
        ? 'You have an unfinished SSO identity change. Leave without completing it?'
        : 'You have an unfinished password change. Leave without updating it?'),
  )

  useEffect(() => {
    if (!reauthContinuation) return
    navigate(reauthContinuation.continuation.returnPath, {
      replace: true,
      state: reauthContinuation.navigationState,
    })
  }, [navigate, reauthContinuation])

  const changePassword = useMutation({
    mutationFn: () =>
      apiFetch<PasswordChangeResponse>('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      }),
    onMutate: () => {
      setPasswordFormError('')
      setPasswordNotice('')
    },
    onSuccess: (result) => {
      setCurrentPassword('')
      setNewPassword('')
      const resultMessage = formatRevocationResult(
        'Password updated.',
        result,
        'All browser sessions and API tokens were revoked.',
      )
      if (result?.sign_in_required !== false) {
        markLoggedOut()
        navigate('/login', {
          replace: true,
          state: {
            authMessage: `${resultMessage} Sign in again with your new password.`,
          },
        })
      } else {
        setPasswordNotice(resultMessage)
      }
    },
  })
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const validationError = getPasswordChangeValidationError(
      currentPassword,
      newPassword,
    )
    if (validationError) {
      setPasswordFormError(validationError)
      return
    }
    changePassword.mutate()
  }

  return (
    <div className="space-y-4">
      {confirmDiscardPasswordDraft.discardDialog}
      <SettingsPageHeader
        scope="Personal"
        title="My account"
        description="Review your account details, sign-in methods, password, and active sessions."
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          <section className="tl-surface rounded-xl p-4">
            <h2 className="font-display text-xl">Account details</h2>
            {meQuery.data && (
              <div className="mt-3 space-y-1 text-sm">
                <p>
                  <span className="font-semibold">Email:</span>{' '}
                  {meQuery.data.email}
                </p>
                <p>
                  <span className="font-semibold">Base role:</span>{' '}
                  {formatSettingsRoleLabel(meQuery.data.role)}
                </p>
                <p>
                  <span className="font-semibold">Provisioning source:</span>{' '}
                  {resolveProvisioningSourceLabel(ssoProvisioned)}
                </p>
                <p>
                  <span className="font-semibold">Status:</span>{' '}
                  {meQuery.data.is_active ? 'Active' : 'Disabled'}
                </p>
                <p>
                  <span className="font-semibold">Created:</span>{' '}
                  {formatDateTime(meQuery.data.created_at)}
                </p>
              </div>
            )}
          </section>

          <OIDCIdentitySection
            status={oidcStatusQuery.data}
            statusLoading={oidcStatusQuery.isLoading}
            statusError={oidcStatusQuery.error}
            ssoProvisioned={ssoProvisioned}
            passwordLoginEnabled={passwordLoginEnabled}
            linkController={oidcLink}
            unlinkController={oidcUnlink}
            onRetryStatus={() => void oidcStatusQuery.refetch()}
          />
        </div>

        <PasswordManagementSection
          ssoProvisioned={ssoProvisioned}
          passwordLoginEnabled={passwordLoginEnabled}
          providerName={oidcStatusQuery.data?.provider_name}
          currentPassword={currentPassword}
          newPassword={newPassword}
          passwordFormError={passwordFormError}
          mutationError={changePassword.isError ? changePassword.error : null}
          successNotice={passwordNotice}
          isPending={changePassword.isPending}
          onCurrentPasswordChange={(value) => {
            setCurrentPassword(value)
            setPasswordFormError('')
          }}
          onNewPasswordChange={(value) => {
            setNewPassword(value)
            setPasswordFormError('')
          }}
          onSubmit={onSubmit}
        />
      </div>
      <AccountSecuritySection onDraftWarningChange={setSecurityDraftWarning} />
    </div>
  )
}

function OIDCIdentitySection({
  status,
  statusLoading,
  statusError,
  ssoProvisioned,
  passwordLoginEnabled,
  linkController,
  unlinkController,
  onRetryStatus,
}: {
  status?: OIDCAccountStatus
  statusLoading: boolean
  statusError: unknown
  ssoProvisioned: boolean
  passwordLoginEnabled: boolean
  linkController: OIDCLinkController
  unlinkController: OIDCUnlinkController
  onRetryStatus: () => void
}) {
  const linkNotice = resolveOidcAccountNotice()
  const providerName = status?.provider_name || 'the identity provider'

  return (
    <>
      <section className="tl-surface rounded-xl p-4">
        <h2 className="font-display text-xl">Sign-in methods</h2>
        {statusLoading && (
          <p className="mt-2 text-sm text-slate dark:text-slate-300">
            Loading sign-in methods...
          </p>
        )}
        {Boolean(statusError) && (
          <div
            role="alert"
            className="mt-3 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
          >
            <p>
              {resolveApiErrorMessage(
                statusError,
                'Sign-in methods could not be loaded',
              )}
            </p>
            <button
              type="button"
              className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
              onClick={onRetryStatus}
            >
              Retry sign-in methods
            </button>
          </div>
        )}
        {status && (
          <div className="mt-3 text-sm">
            {status.linked ? (
              <>
                <p>
                  Linked to{' '}
                  <span className="font-semibold">
                    {status.provider_name || 'OIDC'}
                  </span>
                  {status.linked_email ? ` as ${status.linked_email}` : ''}.
                </p>
                <p
                  className={`mt-2 rounded border px-3 py-2 ${
                    status.available
                      ? 'border-green-300/60 bg-green-50 text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-200'
                      : 'border-amber-300/60 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200'
                  }`}
                >
                  {status.available
                    ? 'SSO sign-in is currently available through this linked identity.'
                    : 'The identity remains linked, but SSO sign-in is currently unavailable because the provider is disabled or unavailable.'}
                </p>
                {status.linked_at && (
                  <p className="mt-1 text-slate dark:text-slate-300">
                    Linked {formatDateTime(status.linked_at)}
                  </p>
                )}
                {ssoProvisioned ? (
                  <p className="mt-3 text-slate dark:text-slate-300">
                    This account and its sign-in identity are managed by{' '}
                    {providerName}.
                  </p>
                ) : passwordLoginEnabled ? (
                  <button
                    type="button"
                    className="mt-4 min-h-11 rounded border border-red-300/70 px-3 py-2 font-semibold text-red-700 dark:border-red-500/40 dark:text-red-200"
                    onClick={unlinkController.openDialog}
                    aria-label={`Unlink ${providerName} from this account`}
                  >
                    Unlink SSO
                  </button>
                ) : (
                  <p className="mt-3 text-slate dark:text-slate-300">
                    This is the only sign-in method for the account. An
                    administrator must set a local password before it can be
                    unlinked.
                  </p>
                )}
              </>
            ) : status.available ? (
              <>
                <p className="text-slate dark:text-slate-300">
                  Link {providerName} as another sign-in method for this
                  account.
                </p>
                {passwordLoginEnabled && status.password_login_enabled ? (
                  <button
                    type="button"
                    className="mt-3 rounded bg-ink px-3 py-2 font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
                    onClick={linkController.openDialog}
                  >
                    Link SSO account
                  </button>
                ) : (
                  <p className="mt-3 text-slate dark:text-slate-300">
                    A local ThreatLens password is required before an SSO
                    identity can be linked.
                  </p>
                )}
              </>
            ) : (
              <p className="text-slate dark:text-slate-300">
                Single sign-on is not currently available.
              </p>
            )}
            {unlinkController.successMessage && (
              <p
                role="status"
                aria-live="polite"
                className="mt-2 text-sm text-green-700 dark:text-green-400"
              >
                {unlinkController.successMessage}
              </p>
            )}
          </div>
        )}
        {linkNotice && (
          <p
            role={linkNotice.error ? 'alert' : 'status'}
            className="mt-3 text-sm text-slate dark:text-slate-200"
          >
            {linkNotice.message}
          </p>
        )}
      </section>

      <OIDCLinkDialog providerName={providerName} controller={linkController} />
      <OIDCUnlinkDialog
        providerName={providerName}
        linkedEmail={status?.linked_email}
        controller={unlinkController}
      />
    </>
  )
}

function useOIDCLinkController() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [draft, setDraft] = useState<OIDCLinkDraft>(EMPTY_OIDC_LINK_DRAFT)
  const [validationErrors, setValidationErrors] =
    useState<OIDCLinkValidationErrors>({})
  const [requestError, setRequestError] = useState<string | null>(null)
  const mfaQuery = useQuery({
    queryKey: MFA_STATUS_QUERY_KEY,
    queryFn: () => apiFetch<MFAStatusResponse>('/auth/security/mfa'),
    enabled: dialogOpen,
  })
  const linkMutation = useMutation({
    mutationKey: OIDC_LINK_MUTATION_KEY,
    gcTime: 0,
    mutationFn: () =>
      apiFetch<OIDCStartResponse>('/auth/oidc/link', {
        method: 'POST',
        body: JSON.stringify(
          createOIDCLinkRequest(draft, mfaQuery.data?.enabled === true),
        ),
      }),
    onMutate: () => setRequestError(null),
    onError: (error) =>
      setRequestError(
        formatMutationError(error, 'SSO account linking could not be started.'),
      ),
    onSettled: () =>
      window.setTimeout(
        () => removeSettledMutations(queryClient, OIDC_LINK_MUTATION_KEY),
        0,
      ),
  })

  const resetMutation = () => {
    forgetSettledMutation(
      queryClient,
      OIDC_LINK_MUTATION_KEY,
      linkMutation.reset,
    )
  }

  const resetDialog = () => {
    setDraft(EMPTY_OIDC_LINK_DRAFT)
    setValidationErrors({})
    setRequestError(null)
    resetMutation()
  }

  const openDialog = () => {
    resetDialog()
    setDialogOpen(true)
    void mfaQuery.refetch()
  }

  const cancel = () => {
    if (linkMutation.isPending) return
    setDialogOpen(false)
    resetDialog()
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (mfaQuery.isFetching || !mfaQuery.data || mfaQuery.error) {
      setValidationErrors({
        form: 'ThreatLens could not confirm the current local MFA requirement. Retry the security check.',
      })
      return
    }

    const errors = validateOIDCLinkDraft(draft, mfaQuery.data.enabled)
    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors)
      return
    }

    setValidationErrors({})
    linkMutation.mutate(undefined, {
      onSuccess: ({ authorization_url: authorizationUrl }) => {
        setDialogOpen(false)
        resetDialog()
        window.location.assign(authorizationUrl)
      },
    })
  }

  const updateDraft = (nextDraft: OIDCLinkDraft) => {
    setDraft(nextDraft)
    setValidationErrors({})
    setRequestError(null)
  }

  const retryMfa = () => {
    setValidationErrors({})
    void mfaQuery.refetch()
  }

  return {
    dialogOpen,
    draft,
    validationErrors,
    mfaStatus: mfaQuery.data,
    mfaStatusError: mfaQuery.error,
    mfaStatusLoading: mfaQuery.isFetching || !mfaQuery.data,
    requestError,
    isPending: linkMutation.isPending,
    isDraftDirty: Boolean(draft.currentPassword || draft.code),
    openDialog,
    updateDraft,
    retryMfa,
    cancel,
    submit,
  }
}

function useOIDCUnlinkController() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [draft, setDraft] = useState<OIDCLinkDraft>(EMPTY_OIDC_LINK_DRAFT)
  const [validationErrors, setValidationErrors] =
    useState<OIDCLinkValidationErrors>({})
  const [requestError, setRequestError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const mfaQuery = useQuery({
    queryKey: MFA_STATUS_QUERY_KEY,
    queryFn: () => apiFetch<MFAStatusResponse>('/auth/security/mfa'),
    enabled: dialogOpen,
  })
  const unlinkMutation = useMutation({
    mutationKey: OIDC_UNLINK_MUTATION_KEY,
    gcTime: 0,
    mutationFn: () =>
      apiFetch<OIDCUnlinkResponse | undefined>('/auth/oidc/account', {
        method: 'DELETE',
        body: JSON.stringify(
          createOIDCUnlinkRequest(draft, mfaQuery.data?.enabled === true),
        ),
      }),
    onMutate: () => {
      setRequestError(null)
      setSuccessMessage(null)
    },
    onError: (error) =>
      setRequestError(
        formatMutationError(error, 'SSO identity could not be unlinked.'),
      ),
    onSuccess: async (result) => {
      setDialogOpen(false)
      setDraft(EMPTY_OIDC_LINK_DRAFT)
      setValidationErrors({})
      setRequestError(null)
      setSuccessMessage(
        formatRevocationResult(
          'SSO identity unlinked.',
          result,
          'This browser session was rotated and other browser sessions were revoked.',
        ),
      )
      await queryClient.invalidateQueries({
        queryKey: ['auth', 'oidc', 'account'],
      })
    },
    onSettled: () =>
      window.setTimeout(
        () => removeSettledMutations(queryClient, OIDC_UNLINK_MUTATION_KEY),
        0,
      ),
  })

  const resetMutation = () => {
    forgetSettledMutation(
      queryClient,
      OIDC_UNLINK_MUTATION_KEY,
      unlinkMutation.reset,
    )
  }
  const resetDialog = () => {
    setDraft(EMPTY_OIDC_LINK_DRAFT)
    setValidationErrors({})
    setRequestError(null)
    resetMutation()
  }
  const openDialog = () => {
    resetDialog()
    setSuccessMessage(null)
    setDialogOpen(true)
    void mfaQuery.refetch()
  }
  const cancel = () => {
    if (unlinkMutation.isPending) return
    setDialogOpen(false)
    resetDialog()
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (mfaQuery.isFetching || !mfaQuery.data || mfaQuery.error) {
      setValidationErrors({
        form: 'ThreatLens could not confirm the current local MFA requirement. Retry the security check.',
      })
      return
    }
    const errors = validateOIDCUnlinkDraft(draft, mfaQuery.data.enabled)
    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors)
      return
    }
    setValidationErrors({})
    unlinkMutation.mutate()
  }
  const updateDraft = (nextDraft: OIDCLinkDraft) => {
    setDraft(nextDraft)
    setValidationErrors({})
    setRequestError(null)
  }
  const retryMfa = () => {
    setValidationErrors({})
    void mfaQuery.refetch()
  }

  return {
    dialogOpen,
    draft,
    validationErrors,
    mfaStatus: mfaQuery.data,
    mfaStatusError: mfaQuery.error,
    mfaStatusLoading: mfaQuery.isFetching || !mfaQuery.data,
    requestError,
    successMessage,
    isPending: unlinkMutation.isPending,
    isDraftDirty: Boolean(draft.currentPassword || draft.code),
    openDialog,
    updateDraft,
    retryMfa,
    cancel,
    submit,
  }
}

function OIDCLinkDialog({
  providerName,
  controller,
}: {
  providerName: string
  controller: OIDCLinkController
}) {
  const {
    dialogOpen,
    draft,
    validationErrors,
    mfaStatus,
    mfaStatusError,
    mfaStatusLoading,
    requestError,
    isPending,
    updateDraft,
    retryMfa,
    cancel,
    submit,
  } = controller
  const passwordInputRef = useRef<HTMLInputElement | null>(null)
  const codeInputRef = useRef<HTMLInputElement | null>(null)
  const mfaRequired = mfaStatus?.enabled === true
  const securityCheckUnavailable =
    Boolean(mfaStatusError) || mfaStatusLoading || !mfaStatus

  useEffect(() => {
    if (!dialogOpen) return
    if (validationErrors.currentPassword) {
      passwordInputRef.current?.focus()
    } else if (validationErrors.code) {
      codeInputRef.current?.focus()
    }
  }, [dialogOpen, validationErrors])

  return (
    <DialogSurface
      open={dialogOpen}
      title={`Link ${providerName}?`}
      description={`Linking adds ${providerName} as an identity-provider-managed sign-in path for this ThreatLens account.`}
      closeLabel="Cancel SSO account linking"
      initialFocusRef={passwordInputRef}
      describeBody={false}
      dismissDisabled={isPending}
      ariaBusy={isPending}
      panelClassName="max-w-xl break-words"
      onClose={cancel}
      footer={
        <>
          <button
            type="button"
            className="rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
            disabled={isPending}
            onClick={cancel}
          >
            Cancel
          </button>
          <button
            type="submit"
            form="oidc-link-form"
            className="min-w-0 break-words rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e]"
            disabled={isPending || securityCheckUnavailable}
          >
            {isPending ? 'Connecting...' : `Continue to ${providerName}`}
          </button>
        </>
      }
    >
      <p>
        Sign-ins through {providerName} follow that provider&apos;s
        authentication and MFA policy. ThreatLens local MFA is not an additional
        prompt for that sign-in path.
      </p>
      <p>
        After this check, {providerName} will require a fresh authentication
        before ThreatLens links the identity.
      </p>
      <form
        id="oidc-link-form"
        className="space-y-3"
        onSubmit={submit}
        noValidate
      >
        <div>
          <label htmlFor="oidc-link-current-password" className="font-semibold">
            Current ThreatLens password
          </label>
          <input
            ref={passwordInputRef}
            id="oidc-link-current-password"
            type="password"
            autoComplete="current-password"
            maxLength={256}
            value={draft.currentPassword}
            onChange={(event) =>
              updateDraft({ ...draft, currentPassword: event.target.value })
            }
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            aria-invalid={Boolean(validationErrors.currentPassword)}
            aria-describedby={
              validationErrors.currentPassword
                ? 'oidc-link-current-password-error'
                : undefined
            }
            disabled={isPending}
            required
          />
          {validationErrors.currentPassword && (
            <p
              id="oidc-link-current-password-error"
              role="alert"
              className="mt-1 text-sm text-red-600 dark:text-red-300"
            >
              {validationErrors.currentPassword}
            </p>
          )}
        </div>

        {mfaStatusLoading && !mfaStatusError && (
          <p
            role="status"
            aria-live="polite"
            className="text-slate dark:text-slate-300"
          >
            Checking the current local MFA requirement...
          </p>
        )}
        {Boolean(mfaStatusError) && (
          <div
            role="alert"
            className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
          >
            <p>
              {resolveApiErrorMessage(
                mfaStatusError,
                'Security requirements could not be loaded',
              )}
            </p>
            <button
              type="button"
              className="mt-2 rounded border border-current px-2 py-1 text-sm font-semibold"
              onClick={retryMfa}
            >
              Retry security check
            </button>
          </div>
        )}

        {mfaRequired && (
          <div>
            <label htmlFor="oidc-link-code" className="font-semibold">
              Current 6-digit authenticator code
            </label>
            <input
              ref={codeInputRef}
              id="oidc-link-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              value={draft.code}
              onChange={(event) =>
                updateDraft({ ...draft, code: event.target.value })
              }
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono dark:border-cyan-900/40 dark:bg-[#072019]"
              aria-invalid={Boolean(validationErrors.code)}
              aria-describedby={
                validationErrors.code
                  ? 'oidc-link-code-help oidc-link-code-error'
                  : 'oidc-link-code-help'
              }
              disabled={isPending}
              required
            />
            <p
              id="oidc-link-code-help"
              className="mt-1 text-xs text-slate dark:text-slate-300"
            >
              Use the authenticator enrolled with ThreatLens. Recovery codes are
              not accepted for account linking.
            </p>
            {validationErrors.code && (
              <p
                id="oidc-link-code-error"
                role="alert"
                className="mt-1 text-sm text-red-600 dark:text-red-300"
              >
                {validationErrors.code}
              </p>
            )}
          </div>
        )}

        {validationErrors.form && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-300">
            {validationErrors.form}
          </p>
        )}
        {Boolean(requestError) && (
          <p
            role="alert"
            aria-live="assertive"
            className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
          >
            {requestError}
          </p>
        )}
      </form>
    </DialogSurface>
  )
}

function OIDCUnlinkDialog({
  providerName,
  linkedEmail,
  controller,
}: {
  providerName: string
  linkedEmail?: string | null
  controller: OIDCUnlinkController
}) {
  const {
    dialogOpen,
    draft,
    validationErrors,
    mfaStatus,
    mfaStatusError,
    mfaStatusLoading,
    requestError,
    isPending,
    updateDraft,
    retryMfa,
    cancel,
    submit,
  } = controller
  const passwordInputRef = useRef<HTMLInputElement | null>(null)
  const codeInputRef = useRef<HTMLInputElement | null>(null)
  const mfaRequired = mfaStatus?.enabled === true
  const securityCheckUnavailable =
    Boolean(mfaStatusError) || mfaStatusLoading || !mfaStatus

  useEffect(() => {
    if (!dialogOpen) return
    if (validationErrors.currentPassword) {
      passwordInputRef.current?.focus()
    } else if (validationErrors.code) {
      codeInputRef.current?.focus()
    }
  }, [dialogOpen, validationErrors])

  return (
    <DialogSurface
      open={dialogOpen}
      title={`Unlink ${providerName}?`}
      description={`This removes ${providerName}${linkedEmail ? ` (${linkedEmail})` : ''} as a sign-in method for this account.`}
      closeLabel="Cancel SSO identity unlinking"
      initialFocusRef={passwordInputRef}
      describeBody={false}
      dismissDisabled={isPending}
      ariaBusy={isPending}
      panelClassName="max-w-xl break-words"
      onClose={cancel}
      footer={
        <>
          <button
            type="button"
            className="rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
            disabled={isPending}
            onClick={cancel}
          >
            Keep SSO linked
          </button>
          <button
            type="submit"
            form="oidc-unlink-form"
            className="tl-button-danger min-w-0 break-words rounded px-3 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60"
            disabled={isPending || securityCheckUnavailable}
          >
            {isPending ? 'Unlinking...' : 'Unlink SSO identity'}
          </button>
        </>
      }
    >
      <div className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
        <p className="font-semibold">Review the sign-in impact</p>
        <ul className="mt-2 list-disc space-y-1 pl-4">
          <li>
            SSO sign-in through {providerName} will stop for this account.
          </li>
          <li>
            The local ThreatLens password remains the account&apos;s sign-in
            method.
          </li>
          <li>
            This browser session will be rotated and other browser sessions will
            be revoked. API tokens are unchanged.
          </li>
        </ul>
      </div>
      <form
        id="oidc-unlink-form"
        className="space-y-3"
        onSubmit={submit}
        noValidate
      >
        <div>
          <label
            htmlFor="oidc-unlink-current-password"
            className="font-semibold"
          >
            Current ThreatLens password
          </label>
          <input
            ref={passwordInputRef}
            id="oidc-unlink-current-password"
            type="password"
            autoComplete="current-password"
            maxLength={256}
            value={draft.currentPassword}
            onChange={(event) =>
              updateDraft({ ...draft, currentPassword: event.target.value })
            }
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            aria-invalid={Boolean(validationErrors.currentPassword)}
            aria-describedby={
              validationErrors.currentPassword
                ? 'oidc-unlink-current-password-error'
                : undefined
            }
            disabled={isPending}
            required
          />
          {validationErrors.currentPassword && (
            <p
              id="oidc-unlink-current-password-error"
              role="alert"
              className="mt-1 text-sm text-red-600 dark:text-red-300"
            >
              {validationErrors.currentPassword}
            </p>
          )}
        </div>

        {mfaStatusLoading && !mfaStatusError && (
          <p
            role="status"
            aria-live="polite"
            className="text-slate dark:text-slate-300"
          >
            Checking the current local MFA requirement...
          </p>
        )}
        {Boolean(mfaStatusError) && (
          <div
            role="alert"
            className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
          >
            <p>
              {resolveApiErrorMessage(
                mfaStatusError,
                'Security requirements could not be loaded',
              )}
            </p>
            <button
              type="button"
              className="mt-2 min-h-11 rounded border border-current px-3 py-2 text-sm font-semibold"
              onClick={retryMfa}
            >
              Retry security check
            </button>
          </div>
        )}

        {mfaRequired && (
          <div>
            <label htmlFor="oidc-unlink-code" className="font-semibold">
              Current authenticator or recovery code
            </label>
            <input
              ref={codeInputRef}
              id="oidc-unlink-code"
              type="text"
              inputMode="text"
              autoComplete="one-time-code"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={64}
              value={draft.code}
              onChange={(event) =>
                updateDraft({ ...draft, code: event.target.value })
              }
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono dark:border-cyan-900/40 dark:bg-[#072019]"
              aria-invalid={Boolean(validationErrors.code)}
              aria-describedby={
                validationErrors.code
                  ? 'oidc-unlink-code-help oidc-unlink-code-error'
                  : 'oidc-unlink-code-help'
              }
              disabled={isPending}
              required
            />
            <p
              id="oidc-unlink-code-help"
              className="mt-1 text-xs text-slate dark:text-slate-300"
            >
              Recovery codes are single use. Spaces and hyphens are accepted
              when present in the code.
            </p>
            {validationErrors.code && (
              <p
                id="oidc-unlink-code-error"
                role="alert"
                className="mt-1 text-sm text-red-600 dark:text-red-300"
              >
                {validationErrors.code}
              </p>
            )}
          </div>
        )}

        {validationErrors.form && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-300">
            {validationErrors.form}
          </p>
        )}
        {requestError && (
          <p
            role="alert"
            aria-live="assertive"
            className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
          >
            {requestError}
          </p>
        )}
      </form>
    </DialogSurface>
  )
}

function resolveProvisioningSourceLabel(ssoProvisioned: boolean) {
  return ssoProvisioned ? 'Single sign-on (OIDC)' : 'Local'
}

function getPasswordChangeValidationError(
  currentPassword: string,
  newPassword: string,
) {
  if (!currentPassword) {
    return 'Enter your current password.'
  }
  if (!newPassword) {
    return 'Enter a new password.'
  }
  if (newPassword.length < 8) {
    return 'New password must be at least 8 characters.'
  }
  return ''
}

function validateOIDCLinkDraft(
  draft: OIDCLinkDraft,
  mfaRequired: boolean,
): OIDCLinkValidationErrors {
  const errors: OIDCLinkValidationErrors = {}
  if (!draft.currentPassword) {
    errors.currentPassword = 'Enter your current ThreatLens password.'
  }
  if (mfaRequired && !/^\d{6}$/.test(draft.code)) {
    errors.code =
      'Enter the current 6-digit code from your ThreatLens authenticator.'
  }
  return errors
}

function validateOIDCUnlinkDraft(
  draft: OIDCLinkDraft,
  mfaRequired: boolean,
): OIDCLinkValidationErrors {
  const errors: OIDCLinkValidationErrors = {}
  if (!draft.currentPassword) {
    errors.currentPassword = 'Enter your current ThreatLens password.'
  }
  if (mfaRequired && !draft.code.trim()) {
    errors.code = 'Enter a current authenticator or recovery code.'
  }
  return errors
}

function createOIDCLinkRequest(
  draft: OIDCLinkDraft,
  mfaRequired: boolean,
): OIDCLinkStartRequest {
  return {
    current_password: draft.currentPassword,
    ...(mfaRequired ? { code: draft.code } : {}),
  }
}

function createOIDCUnlinkRequest(
  draft: OIDCLinkDraft,
  mfaRequired: boolean,
): OIDCUnlinkRequest {
  return {
    current_password: draft.currentPassword,
    ...(mfaRequired ? { code: draft.code.trim() } : {}),
  }
}

function formatRevocationResult(
  message: string,
  result?: { revoked_auth_sessions?: number; revoked_api_tokens?: number },
  fallbackImpact = '',
): string {
  const hasSessionCount = typeof result?.revoked_auth_sessions === 'number'
  const hasTokenCount = typeof result?.revoked_api_tokens === 'number'
  if (!hasSessionCount && !hasTokenCount)
    return [message, fallbackImpact].filter(Boolean).join(' ')

  const details: string[] = []
  if (hasSessionCount) {
    const count = result.revoked_auth_sessions as number
    details.push(`${count} browser session${count === 1 ? '' : 's'} revoked`)
  }
  if (hasTokenCount) {
    const count = result.revoked_api_tokens as number
    details.push(`${count} API token${count === 1 ? '' : 's'} revoked`)
  }
  return `${message} ${details.join(' and ')}.`
}

function formatMutationError(error: unknown, fallback: string) {
  return resolveApiErrorMessage(error, fallback)
}

function resolveOidcAccountNotice(): OIDCCallbackNotice | null {
  if (typeof window === 'undefined') {
    return null
  }
  const params = new URLSearchParams(window.location.search)
  const linkResult = params.get('oidc_link')
  if (linkResult) return resolveOIDCLinkNotice(linkResult)
  const reauthResult = params.get('oidc_reauth')
  return reauthResult ? resolveOIDCReauthNotice(reauthResult) : null
}
