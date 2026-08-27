import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import {
  AuthSessionListResponse,
  CurrentAuthentication,
  MFAStatusResponse,
  OIDCProviderSettings,
  OIDCProviderTestResponse,
  RecentAuthenticationResponse,
  User,
} from '../types/api'
import {
  LocalRecentAuthDraft,
  OIDCProviderActions,
  OIDCProviderRevisionConflict,
  OIDCProviderSaveDialog,
  OIDCProviderSecurityGate,
} from './OIDCProviderChangeControls'
import { resolvePrivilegedSessionState } from './authSessionModel'
import {
  resolveOIDCReauthNotice,
  resolveOIDCReauthStartError,
  type OIDCCallbackNotice,
} from './oidcCallbackMessages'
import {
  beginOIDCReauthentication,
  readOIDCReauthNavigationState,
} from './oidcReauthentication'
import {
  DEFAULT_OIDC_DRAFT,
  OIDCSettingsDraft,
  createOIDCDraft,
  createOIDCRequest,
  oidcDraftFingerprint,
  rebaseOIDCDraft,
  validateOIDCDraftIssue,
} from './oidcSettingsDraft'

const inputClass =
  'mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]'
const EMPTY_LOCAL_RECENT_AUTH_DRAFT: LocalRecentAuthDraft = {
  currentPassword: '',
  code: '',
}

export function IdentitySettingsPage() {
  const queryClient = useQueryClient()
  const location = useLocation()
  const currentUserQuery = useCurrentUser()
  const [draft, setDraft] = useState<OIDCSettingsDraft>(DEFAULT_OIDC_DRAFT)
  const [baselineSettings, setBaselineSettings] =
    useState<OIDCProviderSettings | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [formErrorFieldId, setFormErrorFieldId] = useState<string | null>(null)
  const [revisionConflict, setRevisionConflict] =
    useState<OIDCProviderRevisionConflict | null>(null)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [localRecentAuthDraft, setLocalRecentAuthDraft] = useState(
    EMPTY_LOCAL_RECENT_AUTH_DRAFT,
  )
  const initializedForProviderRef = useRef<string | null>(null)
  const revisionConflictMessageRef = useRef<string | null>(null)
  const providerQuery = useQuery({
    queryKey: ['auth', 'oidc', 'provider'],
    queryFn: () => apiFetch<OIDCProviderSettings>('/auth/oidc/provider'),
  })
  const providerDataStale = isProviderDataStale(
    providerQuery.isError,
    providerQuery.data,
  )
  const sessionsQuery = useQuery({
    queryKey: ['auth', 'security', 'sessions'],
    queryFn: () => apiFetch<AuthSessionListResponse>('/auth/security/sessions'),
  })
  const ownMfaQuery = useQuery({
    queryKey: ['auth', 'security', 'mfa'],
    queryFn: () => apiFetch<MFAStatusResponse>('/auth/security/mfa'),
  })
  const baselineDraft = useMemo(
    () =>
      baselineSettings ? createOIDCDraft(baselineSettings) : DEFAULT_OIDC_DRAFT,
    [baselineSettings],
  )
  const hasUnsavedChanges =
    oidcDraftFingerprint(draft) !== oidcDraftFingerprint(baselineDraft) ||
    Boolean(draft.clientSecret)
  const unsavedChanges = useUnsavedChangesWarning(
    hasUnsavedChanges,
    'You have unsaved identity provider changes. Leave without saving them?',
  )

  useEffect(() => {
    if (!providerQuery.data) {
      return
    }
    const providerKey = providerRevisionKey(providerQuery.data)
    if (initializedForProviderRef.current === providerKey) {
      return
    }
    const nextDraft = createOIDCDraft(providerQuery.data)
    if (baselineSettings && hasUnsavedChanges) {
      setReviewOpen(false)
      setRevisionConflict((current) => ({
        baseline: current?.baseline ?? baselineSettings,
        latest: providerQuery.data ?? null,
        saveError: current?.saveError ?? null,
        refreshError: null,
      }))
      initializedForProviderRef.current = providerKey
      return
    }
    setDraft(nextDraft)
    setBaselineSettings(providerQuery.data)
    setRevisionConflict(null)
    initializedForProviderRef.current = providerKey
  }, [baselineSettings, hasUnsavedChanges, providerQuery.data])

  useEffect(() => {
    if (providerDataStale) setReviewOpen(false)
  }, [providerDataStale])

  const reauthNavigation = readOIDCReauthNavigationState(
    location.state,
    'oidc_provider_update',
  )
  const callbackNotice = reauthNavigation
    ? resolveOIDCReauthNotice(reauthNavigation.result)
    : null
  const reauthenticate = useMutation({
    mutationFn: () =>
      beginOIDCReauthentication({
        returnPath: '/settings/identity',
        purpose: 'oidc_provider_update',
      }),
  })
  const localReauthenticate = useMutation({
    mutationFn: () =>
      apiFetch<RecentAuthenticationResponse>('/auth/security/reauthenticate', {
        method: 'POST',
        body: JSON.stringify({
          current_password: localRecentAuthDraft.currentPassword,
          ...(localRecentAuthDraft.code.trim()
            ? { code: localRecentAuthDraft.code.trim() }
            : {}),
        }),
      }),
    onSuccess: async () => {
      setLocalRecentAuthDraft(EMPTY_LOCAL_RECENT_AUTH_DRAFT)
      await Promise.all([
        currentUserQuery.refetch(),
        sessionsQuery.refetch(),
        ownMfaQuery.refetch(),
      ])
    },
  })
  const reauthNotice: OIDCCallbackNotice | null = reauthenticate.isError
    ? {
        error: true,
        message: resolveOIDCReauthStartError(reauthenticate.error),
      }
    : callbackNotice
  const authentication = currentUserQuery.data?.authentication
  const privilegedSession = resolvePrivilegedSessionState(
    authentication,
    sessionsQuery.data,
    callbackNotice?.error === false,
  )
  const providerAccess = resolveProviderChangeAccess(
    authentication,
    privilegedSession.recentAuthenticationValid,
    reauthNotice,
  )
  const providerChangesAllowed = providerAccess.allowed
  const modernAuthenticationAvailable = Boolean(authentication)
  const securityLoading =
    currentUserQuery.isLoading ||
    (!modernAuthenticationAvailable && sessionsQuery.isLoading)
  const securityFetching =
    currentUserQuery.isFetching ||
    sessionsQuery.isFetching ||
    ownMfaQuery.isFetching
  const securityError =
    currentUserQuery.error ||
    (!modernAuthenticationAvailable ? sessionsQuery.error : null)

  const validationIssue = useMemo(
    () =>
      validateOIDCDraftIssue(
        draft,
        providerQuery.data?.has_client_secret ?? false,
      ),
    [draft, providerQuery.data?.has_client_secret],
  )
  const saveProvider = useMutation({
    mutationFn: () =>
      apiFetch<OIDCProviderSettings>('/auth/oidc/provider', {
        method: 'PUT',
        body: JSON.stringify(
          createOIDCRequest(draft, baselineSettings?.config_revision ?? 0),
        ),
      }),
    onMutate: () => {
      setFormError(null)
      setFormErrorFieldId(null)
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(['auth', 'oidc', 'provider'], saved)
      const nextDraft = createOIDCDraft(saved)
      setDraft(nextDraft)
      setBaselineSettings(saved)
      initializedForProviderRef.current = providerRevisionKey(saved)
      revisionConflictMessageRef.current = null
      setRevisionConflict(null)
      setReviewOpen(false)
    },
    onError: (error) => {
      if (isOIDCMfaAssuranceRequired(error)) {
        setReviewOpen(false)
        setFormError(
          resolveApiErrorMessage(
            error,
            'Identity-provider MFA is required. Complete MFA at the identity provider, then verify with SSO again.',
          ),
        )
        void currentUserQuery.refetch()
        return
      }
      if (isRecentAuthenticationRequired(error)) {
        setReviewOpen(false)
        setFormError(
          resolveApiErrorMessage(
            error,
            'Administrator verification expired before the provider change could be saved',
          ),
        )
        void currentUserQuery.refetch()
        return
      }
      if (!isProviderRevisionConflict(error)) return
      setReviewOpen(false)
      if (baselineSettings) {
        const saveError = resolveApiErrorMessage(
          error,
          'Identity provider settings changed on the server',
        )
        revisionConflictMessageRef.current = saveError
        setRevisionConflict({
          baseline: baselineSettings,
          latest: null,
          saveError,
          refreshError: null,
        })
        void refreshLatestProvider(baselineSettings, saveError)
      }
    },
  })
  const testProvider = useMutation({
    mutationFn: () =>
      apiFetch<OIDCProviderTestResponse>('/auth/oidc/provider/test', {
        method: 'POST',
      }),
  })

  const updateDraft = (
    updater: (current: OIDCSettingsDraft) => OIDCSettingsDraft,
  ) => {
    setFormError(null)
    setFormErrorFieldId(null)
    if (!saveProvider.isPending) saveProvider.reset()
    if (!testProvider.isPending) testProvider.reset()
    setDraft(updater)
  }

  const submit = () => {
    if (providerDataStale) {
      setFormError(
        'Refresh the identity settings successfully before reviewing or saving changes.',
      )
      return
    }
    if (revisionConflict) {
      setFormError(
        'Resolve the provider revision conflict before reviewing this draft.',
      )
      return
    }
    if (!providerChangesAllowed) {
      setFormError(
        'Verify the current administrator session before reviewing identity-provider changes.',
      )
      return
    }
    if (validationIssue) {
      setFormError(validationIssue.message)
      setFormErrorFieldId(validationIssue.fieldId)
      window.requestAnimationFrame(() =>
        document.getElementById(validationIssue.fieldId)?.focus(),
      )
      return
    }
    if (!hasUnsavedChanges) return
    setReviewOpen(true)
  }

  const refreshLatestProvider = async (
    conflictBaseline: OIDCProviderSettings | null,
    saveError?: string | null,
  ) => {
    if (!conflictBaseline) return
    try {
      const latest = await apiFetch<OIDCProviderSettings>('/auth/oidc/provider')
      queryClient.setQueryData(['auth', 'oidc', 'provider'], latest)
      initializedForProviderRef.current = providerRevisionKey(latest)
      setRevisionConflict((current) => ({
        baseline: conflictBaseline,
        latest,
        saveError: saveError ?? current?.saveError ?? null,
        refreshError: null,
      }))
    } catch (error) {
      setRevisionConflict((current) => ({
        baseline: conflictBaseline,
        latest: null,
        saveError: saveError ?? current?.saveError ?? null,
        refreshError: resolveApiErrorMessage(
          error,
          'The latest identity settings could not be loaded',
        ),
      }))
    }
  }

  const reloadConflict = () => {
    if (!revisionConflict?.latest) return
    const nextDraft = createOIDCDraft(revisionConflict.latest)
    setDraft(nextDraft)
    setBaselineSettings(revisionConflict.latest)
    initializedForProviderRef.current = providerRevisionKey(
      revisionConflict.latest,
    )
    revisionConflictMessageRef.current = null
    setRevisionConflict(null)
  }

  const rebaseConflict = () => {
    if (!revisionConflict?.latest) return
    const rebased = rebaseOIDCDraft(
      createOIDCDraft(revisionConflict.baseline),
      draft,
      createOIDCDraft(revisionConflict.latest),
    )
    setDraft(rebased)
    setBaselineSettings(revisionConflict.latest)
    initializedForProviderRef.current = providerRevisionKey(
      revisionConflict.latest,
    )
    revisionConflictMessageRef.current = null
    setRevisionConflict(null)
  }

  return (
    <div className="space-y-4">
      {unsavedChanges.discardDialog}
      <header className="tl-surface rounded-xl p-4">
        <h2 className="font-display text-xl">Identity Provider</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          Configure OpenID Connect sign-in, account provisioning, and role
          assignment.
        </p>
      </header>

      <ProviderQueryStatus
        loading={providerQuery.isLoading}
        error={providerQuery.isError ? providerQuery.error : null}
        stale={providerDataStale}
        fetching={providerQuery.isFetching}
        onRetry={() => void providerQuery.refetch()}
      />

      {providerQuery.data && (
        <>
          <section
            className="tl-surface rounded-xl p-4"
            aria-labelledby="oidc-session-verification-heading"
          >
            <h3
              id="oidc-session-verification-heading"
              className="font-display text-lg"
            >
              Administrator verification
            </h3>
            <div className="mt-3">
              <OIDCProviderSecurityGate
                authentication={providerAccess.gateAuthentication}
                sessions={sessionsQuery.data}
                ownMfa={ownMfaQuery.data}
                loading={securityLoading}
                fetching={securityFetching}
                error={securityError}
                ownMfaLoading={ownMfaQuery.isLoading}
                ownMfaError={ownMfaQuery.error}
                reauthNotice={providerAccess.gateNotice}
                reauthPending={reauthenticate.isPending}
                localDraft={localRecentAuthDraft}
                localError={
                  localReauthenticate.isError
                    ? resolveApiErrorMessage(
                        localReauthenticate.error,
                        'Local administrator verification failed',
                      )
                    : null
                }
                localPending={localReauthenticate.isPending}
                onRetry={() => {
                  void currentUserQuery.refetch()
                  void sessionsQuery.refetch()
                  void ownMfaQuery.refetch()
                }}
                onReauthenticate={() => reauthenticate.mutate()}
                onLocalDraftChange={(nextDraft) => {
                  setLocalRecentAuthDraft(nextDraft)
                  if (!localReauthenticate.isPending)
                    localReauthenticate.reset()
                }}
                onLocalReauthenticate={() => localReauthenticate.mutate()}
              />
            </div>
          </section>

          <fieldset
            disabled={!providerChangesAllowed || providerDataStale}
            className="contents"
          >
            <section className="tl-surface rounded-xl p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h3 className="font-display text-lg">Connection</h3>
                  <p className="mt-1 text-sm text-slate dark:text-slate-300">
                    Register the callback URL exactly with the provider.
                  </p>
                </div>
                <label className="flex min-h-11 items-center gap-2 text-sm font-semibold sm:min-h-0">
                  <input
                    type="checkbox"
                    className="h-5 w-5 sm:h-4 sm:w-4"
                    checked={draft.enabled}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        enabled: event.target.checked,
                      }))
                    }
                  />
                  Enabled
                </label>
              </div>

              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <Field label="Display name" htmlFor="oidc-name">
                  <input
                    id="oidc-name"
                    className={inputClass}
                    value={draft.name}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        name: event.target.value,
                      }))
                    }
                    aria-invalid={formErrorFieldId === 'oidc-name'}
                    aria-errormessage={
                      formErrorFieldId === 'oidc-name'
                        ? 'oidc-form-error'
                        : undefined
                    }
                  />
                </Field>
                <Field label="Issuer URL" htmlFor="oidc-issuer">
                  <input
                    id="oidc-issuer"
                    type="url"
                    className={inputClass}
                    placeholder="https://idp.example.com"
                    value={draft.issuerUrl}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        issuerUrl: event.target.value,
                      }))
                    }
                    aria-invalid={formErrorFieldId === 'oidc-issuer'}
                    aria-errormessage={
                      formErrorFieldId === 'oidc-issuer'
                        ? 'oidc-form-error'
                        : undefined
                    }
                  />
                </Field>
                <Field label="Client ID" htmlFor="oidc-client-id">
                  <input
                    id="oidc-client-id"
                    className={inputClass}
                    value={draft.clientId}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        clientId: event.target.value,
                      }))
                    }
                    aria-invalid={formErrorFieldId === 'oidc-client-id'}
                    aria-errormessage={
                      formErrorFieldId === 'oidc-client-id'
                        ? 'oidc-form-error'
                        : undefined
                    }
                  />
                </Field>
                <Field label="Client authentication" htmlFor="oidc-client-auth">
                  <select
                    id="oidc-client-auth"
                    className={inputClass}
                    value={draft.clientAuthMethod}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        clientAuthMethod: event.target
                          .value as OIDCSettingsDraft['clientAuthMethod'],
                        clearClientSecret:
                          event.target.value === 'none'
                            ? true
                            : current.clearClientSecret,
                      }))
                    }
                  >
                    <option value="client_secret_basic">
                      Client secret (HTTP Basic)
                    </option>
                    <option value="client_secret_post">
                      Client secret (request body)
                    </option>
                    <option value="none">Public client</option>
                  </select>
                </Field>
                {draft.clientAuthMethod !== 'none' && (
                  <Field label="Client secret" htmlFor="oidc-client-secret">
                    <input
                      id="oidc-client-secret"
                      type="password"
                      autoComplete="new-password"
                      className={inputClass}
                      placeholder={
                        providerQuery.data.has_client_secret
                          ? 'Stored secret remains unchanged'
                          : ''
                      }
                      value={draft.clientSecret}
                      onChange={(event) =>
                        updateDraft((current) => ({
                          ...current,
                          clientSecret: event.target.value,
                          clearClientSecret: event.target.value
                            ? false
                            : current.clearClientSecret,
                        }))
                      }
                      aria-invalid={formErrorFieldId === 'oidc-client-secret'}
                      aria-errormessage={
                        formErrorFieldId === 'oidc-client-secret'
                          ? 'oidc-form-error'
                          : undefined
                      }
                    />
                    {providerQuery.data.has_client_secret && (
                      <label className="mt-2 flex min-h-11 items-center gap-2 text-xs text-slate dark:text-slate-300 sm:min-h-0">
                        <input
                          type="checkbox"
                          className="h-5 w-5 sm:h-4 sm:w-4"
                          checked={draft.clearClientSecret}
                          onChange={(event) =>
                            updateDraft((current) => ({
                              ...current,
                              clearClientSecret: event.target.checked,
                              clientSecret: '',
                            }))
                          }
                        />
                        Remove the stored secret on save
                      </label>
                    )}
                  </Field>
                )}
                <Field label="Public ThreatLens URL" htmlFor="oidc-public-url">
                  <input
                    id="oidc-public-url"
                    type="url"
                    className={inputClass}
                    placeholder="https://threatlens.example.com"
                    value={draft.publicBaseUrl}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        publicBaseUrl: event.target.value,
                      }))
                    }
                    aria-invalid={formErrorFieldId === 'oidc-public-url'}
                    aria-errormessage={
                      formErrorFieldId === 'oidc-public-url'
                        ? 'oidc-form-error'
                        : undefined
                    }
                  />
                </Field>
                <Field label="Scopes" htmlFor="oidc-scopes">
                  <input
                    id="oidc-scopes"
                    className={inputClass}
                    value={draft.scopes}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        scopes: event.target.value,
                      }))
                    }
                    aria-invalid={formErrorFieldId === 'oidc-scopes'}
                    aria-errormessage={
                      formErrorFieldId === 'oidc-scopes'
                        ? 'oidc-form-error'
                        : undefined
                    }
                  />
                </Field>
                <div className="lg:col-span-2">
                  <p className="text-sm font-semibold">Callback URL</p>
                  <code className="mt-1 block break-all rounded border border-slate/20 bg-slate/5 px-3 py-2 text-xs dark:border-white/10 dark:bg-white/[0.04]">
                    {resolveCallbackUrl(draft, providerQuery.data)}
                  </code>
                </div>
                {usesInsecureOIDCHttp(draft) && (
                  <p
                    role="alert"
                    className="lg:col-span-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
                  >
                    HTTP can expose authorization codes, tokens, and identity
                    claims in transit. Use it only on a trusted development
                    network with ALLOW_INSECURE_HTTP_OIDC enabled.
                  </p>
                )}
              </div>
            </section>

            <section className="tl-surface rounded-xl p-4">
              <h3 className="font-display text-lg">Provisioning and Roles</h3>
              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <Field label="Role claim" htmlFor="oidc-role-claim">
                  <input
                    id="oidc-role-claim"
                    className={inputClass}
                    value={draft.roleClaim}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        roleClaim: event.target.value,
                      }))
                    }
                    aria-invalid={formErrorFieldId === 'oidc-role-claim'}
                    aria-errormessage={
                      formErrorFieldId === 'oidc-role-claim'
                        ? 'oidc-form-error'
                        : undefined
                    }
                  />
                </Field>
                <Field label="Default role" htmlFor="oidc-default-role">
                  <RoleSelect
                    id="oidc-default-role"
                    value={draft.defaultRole}
                    onChange={(role) =>
                      updateDraft((current) => ({
                        ...current,
                        defaultRole: role,
                      }))
                    }
                  />
                </Field>
              </div>

              <div className="mt-4 space-y-2">
                {draft.roleMappings.map((mapping, index) => (
                  <div
                    key={index}
                    className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_180px_auto] sm:items-end"
                  >
                    <Field
                      label="Exact claim value"
                      htmlFor={`oidc-mapping-${index}`}
                    >
                      <input
                        id={`oidc-mapping-${index}`}
                        className={inputClass}
                        value={mapping.claim_value}
                        aria-invalid={
                          formErrorFieldId === `oidc-mapping-${index}`
                        }
                        aria-errormessage={
                          formErrorFieldId === `oidc-mapping-${index}`
                            ? 'oidc-form-error'
                            : undefined
                        }
                        onChange={(event) =>
                          updateDraft((current) => ({
                            ...current,
                            roleMappings: current.roleMappings.map(
                              (item, itemIndex) =>
                                itemIndex === index
                                  ? { ...item, claim_value: event.target.value }
                                  : item,
                            ),
                          }))
                        }
                      />
                    </Field>
                    <Field
                      label="ThreatLens role"
                      htmlFor={`oidc-mapping-role-${index}`}
                    >
                      <RoleSelect
                        id={`oidc-mapping-role-${index}`}
                        value={mapping.role}
                        onChange={(role) =>
                          updateDraft((current) => ({
                            ...current,
                            roleMappings: current.roleMappings.map(
                              (item, itemIndex) =>
                                itemIndex === index ? { ...item, role } : item,
                            ),
                          }))
                        }
                      />
                    </Field>
                    <button
                      type="button"
                      className="rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-slate dark:border-white/10 dark:text-slate-200"
                      aria-label={`Remove role mapping ${mapping.claim_value || index + 1}`}
                      onClick={() =>
                        updateDraft((current) => ({
                          ...current,
                          roleMappings: current.roleMappings.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        }))
                      }
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className="rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-white/10"
                  onClick={() =>
                    updateDraft((current) => ({
                      ...current,
                      roleMappings: [
                        ...current.roleMappings,
                        { claim_value: '', role: 'viewer' },
                      ],
                    }))
                  }
                >
                  Add role mapping
                </button>
              </div>

              <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <Toggle
                  label="JIT provisioning"
                  checked={draft.jitProvisioningEnabled}
                  onChange={(checked) =>
                    updateDraft((current) => ({
                      ...current,
                      jitProvisioningEnabled: checked,
                      autoApproveUsers: checked
                        ? current.autoApproveUsers
                        : false,
                    }))
                  }
                />
                <Toggle
                  label="Auto-approve JIT users"
                  checked={draft.autoApproveUsers}
                  disabled={!draft.jitProvisioningEnabled}
                  onChange={(checked) =>
                    updateDraft((current) => ({
                      ...current,
                      autoApproveUsers: checked,
                    }))
                  }
                />
                <Toggle
                  label="Sync roles on sign-in"
                  checked={draft.syncRolesOnLogin}
                  onChange={(checked) =>
                    updateDraft((current) => ({
                      ...current,
                      syncRolesOnLogin: checked,
                    }))
                  }
                />
                <Toggle
                  label="Require verified email"
                  checked={draft.requireVerifiedEmail}
                  onChange={(checked) =>
                    updateDraft((current) => ({
                      ...current,
                      requireVerifiedEmail: checked,
                    }))
                  }
                />
                {!draft.requireVerifiedEmail && (
                  <p
                    role="alert"
                    className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200 md:col-span-2 xl:col-span-4"
                  >
                    JIT provisioning will trust unverified email identifiers,
                    including well-formed internal domains such as .local.
                    Missing or malformed email claims are still rejected. Use
                    this only when identity-provider access and email assignment
                    are tightly controlled.
                  </p>
                )}
              </div>
            </section>
          </fieldset>

          <OIDCProviderActions
            conflict={revisionConflict}
            conflictSaveError={revisionConflictMessageRef.current}
            operatorDraft={draft}
            refreshing={providerQuery.isFetching}
            formError={formError}
            saveFailure={resolveProviderSaveFailure(saveProvider.error)}
            savePending={saveProvider.isPending}
            saveSucceeded={saveProvider.isSuccess}
            hasUnsavedChanges={hasUnsavedChanges}
            providerChangesAllowed={providerChangesAllowed && !providerDataStale}
            providerConfigured={providerQuery.data.configured}
            testPending={testProvider.isPending}
            testKeyCount={testProvider.data?.jwks_key_count ?? null}
            testFailure={resolveProviderTestFailure(testProvider.error)}
            onRetryRefresh={() =>
              void refreshLatestProvider(revisionConflict?.baseline ?? null)
            }
            onReload={reloadConflict}
            onRebase={rebaseConflict}
            onReview={submit}
            onTest={() => testProvider.mutate()}
          />
          <OIDCProviderSaveDialog
            open={reviewOpen}
            baseline={baselineDraft}
            draft={draft}
            pending={saveProvider.isPending}
            onCancel={() => setReviewOpen(false)}
            onConfirm={() => {
              if (providerDataStale) {
                setReviewOpen(false)
                setFormError(
                  'Refresh the identity settings successfully before saving changes.',
                )
                return
              }
              saveProvider.mutate()
            }}
          />
        </>
      )}
    </div>
  )
}

function ProviderQueryStatus({
  loading,
  error,
  stale,
  fetching,
  onRetry,
}: {
  loading: boolean
  error: unknown
  stale: boolean
  fetching: boolean
  onRetry: () => void
}) {
  if (loading) {
    return (
      <section className="tl-surface rounded-xl p-4 text-sm">
        Loading identity settings...
      </section>
    )
  }
  if (!error) return null
  return (
    <section
      role="alert"
      className="tl-surface rounded-xl p-4 text-sm text-red-600 dark:text-red-300"
    >
      <p>
        {formatError(
          error,
          stale
            ? 'Identity settings could not be refreshed.'
            : 'Identity settings could not be loaded.',
        )}
      </p>
      {stale && (
        <p className="mt-1">
          Last-known settings remain visible. Editing and saving are disabled until a refresh succeeds.
        </p>
      )}
      <button
        type="button"
        className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
        onClick={onRetry}
        disabled={fetching}
      >
        {fetching ? 'Retrying...' : 'Retry identity settings'}
      </button>
    </section>
  )
}

function resolveProviderChangeAccess(
  authentication: CurrentAuthentication | undefined,
  recentlyAuthenticated: boolean,
  reauthNotice: OIDCCallbackNotice | null,
): {
  allowed: boolean
  gateAuthentication: CurrentAuthentication | undefined
  gateNotice: OIDCCallbackNotice | null
} {
  const oidcMfaAssuranceMissing =
    authentication?.credential_kind === 'opaque_session' &&
    authentication.session_auth_method === 'oidc' &&
    authentication.identity_provider_mfa_asserted !== true
  if (!oidcMfaAssuranceMissing) {
    return {
      allowed: recentlyAuthenticated,
      gateAuthentication: authentication,
      gateNotice: reauthNotice,
    }
  }
  return {
    allowed: false,
    gateAuthentication: {
      ...authentication,
      recently_authenticated: false,
      recent_authentication_valid: false,
    },
    gateNotice: reauthNotice?.error
      ? reauthNotice
      : {
          error: true,
          message:
            'Identity-provider MFA is required for provider changes. Complete MFA at the identity provider, then verify with SSO again.',
        },
  }
}

function isProviderDataStale(
  isError: boolean,
  data: OIDCProviderSettings | undefined,
): boolean {
  return isError && Boolean(data)
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor: string
  children: React.ReactNode
}) {
  return (
    <div className="min-w-0">
      {label && (
        <label htmlFor={htmlFor} className="text-sm font-semibold">
          {label}
        </label>
      )}
      {children}
    </div>
  )
}

function RoleSelect({
  id,
  value,
  onChange,
}: {
  id: string
  value: User['role']
  onChange: (role: User['role']) => void
}) {
  return (
    <select
      id={id}
      className={inputClass}
      value={value}
      onChange={(event) => onChange(event.target.value as User['role'])}
    >
      <option value="viewer">Viewer</option>
      <option value="analyst">Analyst</option>
      <option value="admin">Admin</option>
    </select>
  )
}

function Toggle({
  label,
  checked,
  disabled = false,
  onChange,
}: {
  label: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex min-h-11 items-center gap-2 rounded border border-slate/20 px-3 py-2 text-sm font-semibold sm:min-h-0 dark:border-white/10">
      <input
        type="checkbox"
        className="h-5 w-5 sm:h-4 sm:w-4"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      {label}
    </label>
  )
}

function resolveCallbackUrl(
  draft: OIDCSettingsDraft,
  settings: OIDCProviderSettings,
): string {
  const publicBaseUrl = draft.publicBaseUrl.trim().replace(/\/+$/, '')
  if (settings.public_base_url === publicBaseUrl && settings.callback_url) {
    return settings.callback_url
  }
  return publicBaseUrl
    ? `${publicBaseUrl}${settings.callback_path}`
    : 'Save a public URL to generate the callback.'
}

function usesInsecureOIDCHttp(draft: OIDCSettingsDraft): boolean {
  return [draft.issuerUrl, draft.publicBaseUrl].some((value) =>
    value.trim().toLowerCase().startsWith('http://'),
  )
}

function formatError(error: unknown, fallback: string): string {
  return resolveApiErrorMessage(error, fallback)
}

function providerRevisionKey(settings: OIDCProviderSettings): string {
  return `${settings.id || 'new'}:${settings.config_revision}:${settings.updated_at || 'initial'}`
}

function isProviderRevisionConflict(error: unknown): boolean {
  if (!(error instanceof Error) || error.name !== 'ApiError') return false
  const candidate = error as { code?: unknown; status?: unknown }
  return (
    candidate.code === 'oidc_provider_revision_conflict' &&
    candidate.status === 409
  )
}

function isRecentAuthenticationRequired(error: unknown): boolean {
  if (!(error instanceof Error) || error.name !== 'ApiError') return false
  const code = (error as { code?: unknown }).code
  return (
    code === 'local_reauthentication_required' ||
    code === 'oidc_reauthentication_required' ||
    code === 'browser_session_required' ||
    code === 'opaque_session_required' ||
    code === 'session_inactive' ||
    code === 'account_security_changed'
  )
}

function isOIDCMfaAssuranceRequired(error: unknown): boolean {
  if (!(error instanceof Error) || error.name !== 'ApiError') return false
  return (error as { code?: unknown }).code === 'oidc_mfa_assurance_required'
}

function resolveProviderSaveFailure(error: unknown): string | null {
  if (!error || isProviderRevisionConflict(error)) return null
  return formatError(error, 'Identity settings could not be saved.')
}

function resolveProviderTestFailure(error: unknown): string | null {
  if (!error) return null
  return formatError(error, 'Identity provider test failed.')
}
