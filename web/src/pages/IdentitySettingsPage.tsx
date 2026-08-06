import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { OIDCProviderSettings, OIDCProviderTestResponse, User } from '../types/api'
import {
  DEFAULT_OIDC_DRAFT,
  OIDCSettingsDraft,
  createOIDCDraft,
  createOIDCRequest,
  oidcDraftFingerprint,
  validateOIDCDraft,
} from './oidcSettingsDraft'

const inputClass =
  'mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]'

export function IdentitySettingsPage() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<OIDCSettingsDraft>(DEFAULT_OIDC_DRAFT)
  const [baselineFingerprint, setBaselineFingerprint] = useState(oidcDraftFingerprint(DEFAULT_OIDC_DRAFT))
  const [formError, setFormError] = useState<string | null>(null)
  const initializedForProviderRef = useRef<string | null>(null)
  const providerQuery = useQuery({
    queryKey: ['auth', 'oidc', 'provider'],
    queryFn: () => apiFetch<OIDCProviderSettings>('/auth/oidc/provider'),
  })
  const hasUnsavedChanges = oidcDraftFingerprint(draft) !== baselineFingerprint || Boolean(draft.clientSecret)
  const unsavedChanges = useUnsavedChangesWarning(
    hasUnsavedChanges,
    'You have unsaved identity provider changes. Leave without saving them?',
  )

  useEffect(() => {
    if (!providerQuery.data) {
      return
    }
    const providerKey = `${providerQuery.data.id || 'new'}:${providerQuery.data.updated_at || 'initial'}`
    if (initializedForProviderRef.current === providerKey) {
      return
    }
    const nextDraft = createOIDCDraft(providerQuery.data)
    setDraft(nextDraft)
    setBaselineFingerprint(oidcDraftFingerprint(nextDraft))
    initializedForProviderRef.current = providerKey
  }, [providerQuery.data])

  const validationError = useMemo(
    () => validateOIDCDraft(draft, providerQuery.data?.has_client_secret ?? false),
    [draft, providerQuery.data?.has_client_secret],
  )
  const saveProvider = useMutation({
    mutationFn: () =>
      apiFetch<OIDCProviderSettings>('/auth/oidc/provider', {
        method: 'PUT',
        body: JSON.stringify(createOIDCRequest(draft)),
      }),
    onMutate: () => setFormError(null),
    onSuccess: (saved) => {
      queryClient.setQueryData(['auth', 'oidc', 'provider'], saved)
      const nextDraft = createOIDCDraft(saved)
      setDraft(nextDraft)
      setBaselineFingerprint(oidcDraftFingerprint(nextDraft))
      initializedForProviderRef.current = `${saved.id || 'new'}:${saved.updated_at || 'initial'}`
    },
  })
  const testProvider = useMutation({
    mutationFn: () => apiFetch<OIDCProviderTestResponse>('/auth/oidc/provider/test', { method: 'POST' }),
  })

  const submit = () => {
    if (validationError) {
      setFormError(validationError)
      return
    }
    saveProvider.mutate()
  }

  return (
    <div className="space-y-4">
      {unsavedChanges.discardDialog}
      <header className="tl-surface rounded-xl p-4">
        <h2 className="font-display text-xl">Identity Provider</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          Configure OpenID Connect sign-in, account provisioning, and role assignment.
        </p>
      </header>

      {providerQuery.isLoading && <section className="tl-surface rounded-xl p-4 text-sm">Loading identity settings...</section>}
      {providerQuery.isError && (
        <section role="alert" className="tl-surface rounded-xl p-4 text-sm text-red-600 dark:text-red-300">
          {formatError(providerQuery.error, 'Identity settings could not be loaded.')}
        </section>
      )}

      {providerQuery.data && (
        <>
          <section className="tl-surface rounded-xl p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h3 className="font-display text-lg">Connection</h3>
                <p className="mt-1 text-sm text-slate dark:text-slate-300">Register the callback URL exactly with the provider.</p>
              </div>
              <label className="flex items-center gap-2 text-sm font-semibold">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
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
                  onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                />
              </Field>
              <Field label="Issuer URL" htmlFor="oidc-issuer">
                <input
                  id="oidc-issuer"
                  type="url"
                  className={inputClass}
                  placeholder="https://idp.example.com"
                  value={draft.issuerUrl}
                  onChange={(event) => setDraft((current) => ({ ...current, issuerUrl: event.target.value }))}
                />
              </Field>
              <Field label="Client ID" htmlFor="oidc-client-id">
                <input
                  id="oidc-client-id"
                  className={inputClass}
                  value={draft.clientId}
                  onChange={(event) => setDraft((current) => ({ ...current, clientId: event.target.value }))}
                />
              </Field>
              <Field label="Client authentication" htmlFor="oidc-client-auth">
                <select
                  id="oidc-client-auth"
                  className={inputClass}
                  value={draft.clientAuthMethod}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      clientAuthMethod: event.target.value as OIDCSettingsDraft['clientAuthMethod'],
                      clearClientSecret: event.target.value === 'none' ? true : current.clearClientSecret,
                    }))
                  }
                >
                  <option value="client_secret_basic">Client secret (HTTP Basic)</option>
                  <option value="client_secret_post">Client secret (request body)</option>
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
                    placeholder={providerQuery.data.has_client_secret ? 'Stored secret remains unchanged' : ''}
                    value={draft.clientSecret}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        clientSecret: event.target.value,
                        clearClientSecret: event.target.value ? false : current.clearClientSecret,
                      }))
                    }
                  />
                  {providerQuery.data.has_client_secret && (
                    <label className="mt-2 flex items-center gap-2 text-xs text-slate dark:text-slate-300">
                      <input
                        type="checkbox"
                        checked={draft.clearClientSecret}
                        onChange={(event) =>
                          setDraft((current) => ({ ...current, clearClientSecret: event.target.checked, clientSecret: '' }))
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
                  onChange={(event) => setDraft((current) => ({ ...current, publicBaseUrl: event.target.value }))}
                />
              </Field>
              <Field label="Scopes" htmlFor="oidc-scopes">
                <input
                  id="oidc-scopes"
                  className={inputClass}
                  value={draft.scopes}
                  onChange={(event) => setDraft((current) => ({ ...current, scopes: event.target.value }))}
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
                  HTTP can expose authorization codes, tokens, and identity claims in transit. Use it only on a trusted
                  development network with ALLOW_INSECURE_HTTP_OIDC enabled.
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
                  onChange={(event) => setDraft((current) => ({ ...current, roleClaim: event.target.value }))}
                />
              </Field>
              <Field label="Default role" htmlFor="oidc-default-role">
                <RoleSelect
                  id="oidc-default-role"
                  value={draft.defaultRole}
                  onChange={(role) => setDraft((current) => ({ ...current, defaultRole: role }))}
                />
              </Field>
            </div>

            <div className="mt-4 space-y-2">
              {draft.roleMappings.map((mapping, index) => (
                <div key={`${index}-${mapping.role}`} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_180px_auto] sm:items-end">
                  <Field label={index === 0 ? 'Exact claim value' : ''} htmlFor={`oidc-mapping-${index}`}>
                    <input
                      id={`oidc-mapping-${index}`}
                      className={inputClass}
                      value={mapping.claim_value}
                      onChange={(event) =>
                        setDraft((current) => ({
                          ...current,
                          roleMappings: current.roleMappings.map((item, itemIndex) =>
                            itemIndex === index ? { ...item, claim_value: event.target.value } : item,
                          ),
                        }))
                      }
                    />
                  </Field>
                  <Field label={index === 0 ? 'ThreatLens role' : ''} htmlFor={`oidc-mapping-role-${index}`}>
                    <RoleSelect
                      id={`oidc-mapping-role-${index}`}
                      value={mapping.role}
                      onChange={(role) =>
                        setDraft((current) => ({
                          ...current,
                          roleMappings: current.roleMappings.map((item, itemIndex) =>
                            itemIndex === index ? { ...item, role } : item,
                          ),
                        }))
                      }
                    />
                  </Field>
                  <button
                    type="button"
                    className="rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-slate dark:border-white/10 dark:text-slate-200"
                    onClick={() =>
                      setDraft((current) => ({
                        ...current,
                        roleMappings: current.roleMappings.filter((_, itemIndex) => itemIndex !== index),
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
                  setDraft((current) => ({
                    ...current,
                    roleMappings: [...current.roleMappings, { claim_value: '', role: 'viewer' }],
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
                  setDraft((current) => ({
                    ...current,
                    jitProvisioningEnabled: checked,
                    autoApproveUsers: checked ? current.autoApproveUsers : false,
                  }))
                }
              />
              <Toggle
                label="Auto-approve JIT users"
                checked={draft.autoApproveUsers}
                disabled={!draft.jitProvisioningEnabled}
                onChange={(checked) => setDraft((current) => ({ ...current, autoApproveUsers: checked }))}
              />
              <Toggle
                label="Sync roles on sign-in"
                checked={draft.syncRolesOnLogin}
                onChange={(checked) => setDraft((current) => ({ ...current, syncRolesOnLogin: checked }))}
              />
              <Toggle
                label="Require verified email"
                checked={draft.requireVerifiedEmail}
                onChange={(checked) => setDraft((current) => ({ ...current, requireVerifiedEmail: checked }))}
              />
              {!draft.requireVerifiedEmail && (
                <p
                  role="alert"
                  className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200 md:col-span-2 xl:col-span-4"
                >
                  JIT provisioning will trust unverified email identifiers, including well-formed internal domains such
                  as .local. Missing or malformed email claims are still rejected. Use this only when identity-provider
                  access and email assignment are tightly controlled.
                </p>
              )}
            </div>
          </section>

          <section className="tl-surface rounded-xl p-4">
            {(formError || saveProvider.isError) && (
              <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-300">
                {formError || formatError(saveProvider.error, 'Identity settings could not be saved.')}
              </p>
            )}
            {saveProvider.isSuccess && (
              <p role="status" className="mb-3 text-sm text-green-700 dark:text-green-400">
                Identity provider settings saved.
              </p>
            )}
            {testProvider.isSuccess && (
              <p role="status" className="mb-3 text-sm text-green-700 dark:text-green-400">
                Discovery and {testProvider.data.jwks_key_count} signing key{testProvider.data.jwks_key_count === 1 ? '' : 's'} verified.
              </p>
            )}
            {testProvider.isError && (
              <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-300">
                {formatError(testProvider.error, 'Identity provider test failed.')}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded bg-ink px-4 py-2 font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
                disabled={saveProvider.isPending}
                onClick={submit}
              >
                {saveProvider.isPending ? 'Saving...' : 'Save settings'}
              </button>
              <button
                type="button"
                className="rounded border border-slate/20 px-4 py-2 font-semibold dark:border-white/10"
                disabled={!providerQuery.data.configured || hasUnsavedChanges || testProvider.isPending}
                onClick={() => testProvider.mutate()}
              >
                {testProvider.isPending ? 'Testing...' : 'Test connection'}
              </button>
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
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

function RoleSelect({ id, value, onChange }: { id: string; value: User['role']; onChange: (role: User['role']) => void }) {
  return (
    <select id={id} className={inputClass} value={value} onChange={(event) => onChange(event.target.value as User['role'])}>
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
    <label className="flex items-center gap-2 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-white/10">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  )
}

function resolveCallbackUrl(draft: OIDCSettingsDraft, settings: OIDCProviderSettings): string {
  const publicBaseUrl = draft.publicBaseUrl.trim().replace(/\/+$/, '')
  if (settings.public_base_url === publicBaseUrl && settings.callback_url) {
    return settings.callback_url
  }
  return publicBaseUrl
    ? `${publicBaseUrl}${settings.callback_path}`
    : 'Save a public URL to generate the callback.'
}

function usesInsecureOIDCHttp(draft: OIDCSettingsDraft): boolean {
  return [draft.issuerUrl, draft.publicBaseUrl].some((value) => value.trim().toLowerCase().startsWith('http://'))
}

function formatError(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) {
    return `${fallback} ${error.message}`
  }
  return fallback
}
