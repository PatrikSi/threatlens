import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { formatDateTime } from '../utils/datetime'
import {
  Feed,
  IntegrationConnector,
  NotificationEventType,
  NotificationTemplateVariable,
  SMTPSettings,
  SMTPSettingsUpdateRequest,
  SMTPTestRequest,
  SMTPTestResponse,
  SMTPSecurityMode,
} from '../types/api'
import {
  createSMTPDraftFromSettings,
  createSMTPRequestFromDraft,
  DEFAULT_SMTP_DRAFT,
  getFirstSMTPSettingsDraftValidationError,
  smtpDraftFingerprint,
  SMTPSettingsDraft,
  validateSMTPSettingsDraft,
} from './smtpSettingsDraft'

type NoticeState = {
  tone: 'success' | 'error'
  message: string
}

const SMTP_EVENT_OPTIONS: Array<{ value: NotificationEventType; label: string }> = [
  { value: 'rss_item_new', label: 'New RSS Item' },
  { value: 'alert_match', label: 'Alert Match' },
  { value: 'feed_failing', label: 'Feed Failing' },
  { value: 'webhook_failed', label: 'Webhook Failed' },
  { value: 'daily_digest', label: 'Daily Digest' },
]

export function SMTPIntegrationSettingsPage() {
  const queryClient = useQueryClient()
  const [draft, setDraftState] = useState<SMTPSettingsDraft>(DEFAULT_SMTP_DRAFT)
  const [hasUserEdited, setHasUserEdited] = useState(false)
  const [notice, setNotice] = useState<NoticeState | null>(null)
  const [sendTestEmail, setSendTestEmail] = useState(false)
  const [testRecipient, setTestRecipient] = useState('')
  const [testResult, setTestResult] = useState<SMTPTestResponse | null>(null)

  const connectorsQuery = useQuery({
    queryKey: ['integrations', 'connectors'],
    queryFn: () => apiFetch<IntegrationConnector[]>('/integrations/connectors'),
  })
  const smtpSettingsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'settings'],
    queryFn: () => apiFetch<SMTPSettings>('/integrations/smtp/settings'),
  })
  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })
  const variablesQuery = useQuery({
    queryKey: ['notifications', 'template-variables'],
    queryFn: () => apiFetch<NotificationTemplateVariable[]>('/notifications/template-variables'),
  })

  const smtpSettings = smtpSettingsQuery.data
  const baselineDraft = useMemo(
    () => (smtpSettings ? createSMTPDraftFromSettings(smtpSettings) : DEFAULT_SMTP_DRAFT),
    [smtpSettings],
  )
  const validation = useMemo(() => validateSMTPSettingsDraft(draft), [draft])
  const firstValidationError = getFirstSMTPSettingsDraftValidationError(validation)
  const draftDirty = smtpDraftFingerprint(draft) !== smtpDraftFingerprint(baselineDraft)
  const confirmDiscardUnsavedIntegrationChanges = useUnsavedChangesWarning(
    draftDirty,
    'You have unsaved integration changes. Leave without saving?',
  )
  const smtpConnector = connectorsQuery.data?.find((connector) => connector.integration_type === 'smtp') ?? null
  const feeds = feedsQuery.data ?? []
  const variables = variablesQuery.data ?? []

  const setDraft: Dispatch<SetStateAction<SMTPSettingsDraft>> = (value) => {
    setHasUserEdited(true)
    setTestResult(null)
    setDraftState(value)
  }

  useEffect(() => {
    if (!smtpSettings || hasUserEdited) {
      return
    }
    setDraftState(createSMTPDraftFromSettings(smtpSettings))
  }, [hasUserEdited, smtpSettings])

  const saveSmtp = useMutation({
    mutationKey: ['integrations', 'smtp', 'save'],
    mutationFn: (payload: SMTPSettingsUpdateRequest) =>
      apiFetch<SMTPSettings>('/integrations/smtp/settings', {
        method: 'PUT',
        body: JSON.stringify(payload),
    }),
    onSuccess: (saved) => {
      queryClient.setQueryData(['integrations', 'smtp', 'settings'], saved)
      setDraftState(createSMTPDraftFromSettings(saved))
      setHasUserEdited(false)
      setTestResult(null)
      setNotice({ tone: 'success', message: 'SMTP settings saved.' })
      void queryClient.invalidateQueries({ queryKey: ['integrations'] })
    },
  })

  const testSmtp = useMutation({
    mutationKey: ['integrations', 'smtp', 'test'],
    mutationFn: (payload: SMTPTestRequest) =>
      apiFetch<SMTPTestResponse>('/integrations/smtp/test', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (result) => {
      setTestResult(result)
      setNotice({ tone: result.success ? 'success' : 'error', message: result.success ? 'SMTP test succeeded.' : 'SMTP test failed.' })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp', 'settings'] })
    },
  })

  const onSave = () => {
    if (firstValidationError) {
      setNotice({ tone: 'error', message: firstValidationError })
      return
    }
    setNotice(null)
    saveSmtp.mutate(createSMTPRequestFromDraft(draft))
  }

  const onTest = () => {
    const testError = resolveTestValidationError(draft, sendTestEmail, testRecipient, firstValidationError)
    if (testError) {
      setNotice({ tone: 'error', message: testError })
      return
    }
    setNotice(null)
    testSmtp.mutate({
      send_email: sendTestEmail,
      recipient_email: sendTestEmail ? testRecipient.trim() : null,
      settings: draftDirty ? createSMTPRequestFromDraft(draft) : null,
    })
  }

  if (smtpSettingsQuery.isLoading || connectorsQuery.isLoading || feedsQuery.isLoading || variablesQuery.isLoading) {
    return (
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
        Loading integration settings...
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Settings</p>
        <h2 className="mt-1 font-display text-xl">SMTP Integration</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Send notification emails through a reusable SMTP destination.
        </p>
        {(smtpSettingsQuery.isError || connectorsQuery.isError || feedsQuery.isError || variablesQuery.isError) && (
          <p role="alert" className="mt-3 text-sm text-red-600">
            {resolveApiMessage(
              smtpSettingsQuery.error ?? connectorsQuery.error ?? feedsQuery.error ?? variablesQuery.error,
              'Failed to load integrations.',
            )}
          </p>
        )}
      </section>

      <section id="smtp" className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
          <div className="min-w-0">
            <h3 className="font-display text-lg">SMTP</h3>
            <p className="mt-1 text-sm text-slate dark:text-white/70">
              {smtpConnector?.description ?? 'Send operational emails through an SMTP server.'}
            </p>
          </div>
          {smtpSettings && (
            <span className={`tl-chip ${healthBadgeClass(smtpSettings.health_status)}`}>
              {describeHealthStatus(smtpSettings)}
            </span>
          )}
        </div>

        <div id="smtp-integration-panel" className="mt-4 space-y-5">
            {smtpSettings?.has_unreadable_secret && (
              <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200">
                The saved SMTP password cannot be decrypted. Enter a new password or clear the saved password.
              </div>
            )}

            <div className="grid gap-3 md:grid-cols-4">
              <Metric label="Configured" value={smtpSettings?.configured ? 'Yes' : 'No'} />
              <Metric label="Enabled" value={draft.enabled ? 'Yes' : 'No'} />
              <Metric label="Last Test" value={smtpSettings?.last_test_at ? formatDateTime(smtpSettings.last_test_at) : 'Never'} />
              <Metric
                label="Last Duration"
                value={smtpSettings?.last_test_duration_ms != null ? `${smtpSettings.last_test_duration_ms} ms` : 'n/a'}
              />
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
              <div className="space-y-4">
                <div>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h3 className="font-semibold">SMTP Configuration</h3>
                    <label className="flex items-center gap-2 rounded-full border border-slate/20 px-3 py-1 text-sm dark:border-cyan-900/40">
                      <input
                        type="checkbox"
                        checked={draft.enabled}
                        onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
                      />
                      Enabled
                    </label>
                  </div>

                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    <TextInput
                      id="smtp-host"
                      label="Host"
                      value={draft.host}
                      error={validation.host}
                      placeholder="smtp.example.com"
                      onChange={(value) => setDraft((current) => ({ ...current, host: value }))}
                    />
                    <TextInput
                      id="smtp-port"
                      label="Port"
                      type="number"
                      min={1}
                      max={65535}
                      value={draft.port}
                      error={validation.port}
                      onChange={(value) => setDraft((current) => ({ ...current, port: value }))}
                    />
                    <div>
                      <label htmlFor="smtp-security" className="text-sm font-semibold">
                        Security
                      </label>
                      <select
                        id="smtp-security"
                        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={draft.security}
                        onChange={(event) =>
                          setDraft((current) => ({ ...current, security: event.target.value as SMTPSecurityMode }))
                        }
                      >
                        <option value="starttls">STARTTLS</option>
                        <option value="ssl_tls">SSL/TLS</option>
                        <option value="none">None</option>
                      </select>
                    </div>
                    <TextInput
                      id="smtp-timeout"
                      label="Timeout (seconds)"
                      type="number"
                      min={1}
                      max={60}
                      value={draft.timeout_seconds}
                      error={validation.timeout_seconds}
                      onChange={(value) => setDraft((current) => ({ ...current, timeout_seconds: value }))}
                    />
                    <TextInput
                      id="smtp-username"
                      label="Username"
                      value={draft.username}
                      error={validation.username}
                      autoComplete="username"
                      onChange={(value) => setDraft((current) => ({ ...current, username: value }))}
                    />
                    <TextInput
                      id="smtp-password"
                      label="Password"
                      type="password"
                      value={draft.password}
                      error={validation.password}
                      autoComplete="new-password"
                      placeholder={smtpSettings?.password_configured ? 'Saved password configured' : ''}
                      onChange={(value) =>
                        setDraft((current) => ({ ...current, password: value, clear_password: value ? false : current.clear_password }))
                      }
                    />
                    <TextInput
                      id="smtp-from-email"
                      label="Sender Email"
                      type="email"
                      value={draft.from_email}
                      error={validation.from_email}
                      placeholder="threatlens@example.com"
                      onChange={(value) => setDraft((current) => ({ ...current, from_email: value }))}
                    />
                    <TextInput
                      id="smtp-from-name"
                      label="Sender Name"
                      value={draft.from_name}
                      placeholder="ThreatLens"
                      onChange={(value) => setDraft((current) => ({ ...current, from_name: value }))}
                    />
                    <div className="md:col-span-2">
                      <TextArea
                        id="smtp-to-emails"
                        label="Recipient Emails"
                        value={draft.to_emails}
                        error={validation.to_emails}
                        rows={3}
                        placeholder="analyst@example.com, soc@example.com"
                        helperText="One or more addresses for SMTP notifications and reports."
                        monospace={false}
                        onChange={(value) => setDraft((current) => ({ ...current, to_emails: value }))}
                      />
                    </div>
                  </div>

                  {smtpSettings?.password_configured && (
                    <label className="mt-4 flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={draft.clear_password}
                        disabled={Boolean(draft.password.trim())}
                        onChange={(event) => setDraft((current) => ({ ...current, clear_password: event.target.checked }))}
                      />
                      Clear saved password on save
                    </label>
                  )}

                  <div className="mt-5 rounded-lg border border-slate/20 p-4 dark:border-cyan-900/40">
                    <h3 className="font-semibold">Notification Content</h3>
                    <div className="mt-4 grid gap-4 lg:grid-cols-2">
                      <div>
                        <p className="text-sm font-semibold">Send For</p>
                        <div className="mt-2 grid gap-2">
                          {SMTP_EVENT_OPTIONS.map((option) => (
                            <label key={option.value} className="flex items-center gap-2 text-sm">
                              <input
                                type="checkbox"
                                checked={draft.event_types.includes(option.value)}
                                onChange={(event) =>
                                  setDraft((current) => ({
                                    ...current,
                                    event_types: event.target.checked
                                      ? Array.from(new Set([...current.event_types, option.value]))
                                      : current.event_types.filter((eventType) => eventType !== option.value),
                                  }))
                                }
                              />
                              {option.label}
                            </label>
                          ))}
                        </div>
                        {validation.event_types && <p className="mt-1 text-xs text-red-600">{validation.event_types}</p>}
                      </div>

                      <div>
                        <p className="text-sm font-semibold">Feed Scope</p>
                        <div className="mt-2 inline-flex rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40">
                          <button
                            type="button"
                            aria-pressed={draft.feed_scope === 'all'}
                            className={`rounded px-3 py-1.5 text-sm font-semibold ${
                              draft.feed_scope === 'all' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'
                            }`}
                            onClick={() => setDraft((current) => ({ ...current, feed_scope: 'all', feed_ids: [] }))}
                          >
                            All Feeds
                          </button>
                          <button
                            type="button"
                            aria-pressed={draft.feed_scope === 'selected'}
                            className={`rounded px-3 py-1.5 text-sm font-semibold ${
                              draft.feed_scope === 'selected'
                                ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                                : 'text-slate dark:text-white/75'
                            }`}
                            onClick={() => setDraft((current) => ({ ...current, feed_scope: 'selected' }))}
                          >
                            Selected Feeds
                          </button>
                        </div>

                        {draft.feed_scope === 'selected' && (
                          <div className="mt-3 max-h-44 overflow-auto rounded border border-slate/20 p-2 dark:border-cyan-900/40">
                            {feeds.length ? (
                              feeds.map((feed) => (
                                <label key={feed.id} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-slate/5 dark:hover:bg-white/[0.04]">
                                  <input
                                    type="checkbox"
                                    checked={draft.feed_ids.includes(feed.id)}
                                    onChange={(event) =>
                                      setDraft((current) => ({
                                        ...current,
                                        feed_ids: event.target.checked
                                          ? Array.from(new Set([...current.feed_ids, feed.id]))
                                          : current.feed_ids.filter((feedId) => feedId !== feed.id),
                                      }))
                                    }
                                  />
                                  <span className="min-w-0 truncate">{feed.name}</span>
                                </label>
                              ))
                            ) : (
                              <p className="px-2 py-1.5 text-sm text-slate dark:text-white/70">No feeds are available.</p>
                            )}
                          </div>
                        )}
                        {validation.feed_ids && <p className="mt-1 text-xs text-red-600">{validation.feed_ids}</p>}
                      </div>
                    </div>

                    <div className="mt-4 grid gap-4">
                      <TextInput
                        id="smtp-subject-template"
                        label="Email Subject Template"
                        value={draft.subject_template}
                        error={validation.subject_template}
                        onChange={(value) => setDraft((current) => ({ ...current, subject_template: value }))}
                      />
                      <TextArea
                        id="smtp-html-template"
                        label="Email HTML Template"
                        value={draft.html_template}
                        error={validation.html_template}
                        rows={9}
                        onChange={(value) => setDraft((current) => ({ ...current, html_template: value }))}
                      />
                    </div>
                  </div>

                  <div className="mt-5 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className="rounded bg-ink px-3 py-2 text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
                      disabled={saveSmtp.isPending}
                      onClick={onSave}
                    >
                      {saveSmtp.isPending ? 'Saving...' : 'Save SMTP'}
                    </button>
                    <button
                      type="button"
                      className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                      disabled={testSmtp.isPending}
                      onClick={onTest}
                    >
                      {testSmtp.isPending ? 'Testing...' : 'Test SMTP'}
                    </button>
                  </div>
                </div>
              </div>

              <aside className="space-y-4">
                <div className="border-t border-slate/20 pt-4 dark:border-cyan-900/40 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
                  <h3 className="font-semibold">Test Delivery</h3>
                  <label className="mt-3 flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={sendTestEmail}
                      onChange={(event) => {
                        setSendTestEmail(event.target.checked)
                        setTestResult(null)
                      }}
                    />
                    Send a test email
                  </label>
                  {sendTestEmail && (
                    <div className="mt-3">
                      <TextInput
                        id="smtp-test-recipient"
                        label="Recipient Email"
                        type="email"
                        value={testRecipient}
                        placeholder="analyst@example.com"
                        onChange={setTestRecipient}
                      />
                    </div>
                  )}
                  <p className="mt-2 text-xs text-slate dark:text-white/60">
                    {sendTestEmail
                      ? 'Sends a rendered HTML email with the current template and form values.'
                      : 'Runs connection and authentication only.'}
                  </p>
                </div>

                <div className="border-t border-slate/20 pt-4 dark:border-cyan-900/40">
                  <h3 className="font-semibold">Test Result</h3>
                  {testResult ? (
                    <div className="mt-3 space-y-2 text-sm">
                      <span className={`tl-chip ${testResult.success ? 'tl-chip-success' : 'tl-chip-danger'}`}>
                        {testResult.success ? 'Success' : 'Failed'}
                      </span>
                      <p>Action: {testResult.action === 'send' ? 'Send test email' : 'Connection test'}</p>
                      <p>Duration: {testResult.duration_ms != null ? `${testResult.duration_ms} ms` : 'n/a'}</p>
                      {testResult.used_unsaved_settings && (
                        <p className="text-xs text-slate dark:text-white/60">Result used the current unsaved form values.</p>
                      )}
                      {testResult.server_message && (
                        <code className="block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{testResult.server_message}</code>
                      )}
                      {testResult.error && <p className="text-red-600">{testResult.error}</p>}
                    </div>
                  ) : (
                    <p className="mt-2 text-sm text-slate dark:text-white/70">No SMTP test has run in this session.</p>
                  )}
                </div>

                <div className="border-t border-slate/20 pt-4 dark:border-cyan-900/40">
                  <h3 className="font-semibold">Template Variables</h3>
                  <div className="mt-3 max-h-56 overflow-auto rounded border border-slate/20 p-2 dark:border-cyan-900/40">
                    {variables.length ? (
                      variables.map((variable) => (
                        <div key={variable.key} className="rounded px-2 py-1.5 text-xs">
                          <code className="font-semibold">{`{{ ${variable.key} }}`}</code>
                          <p className="mt-0.5 text-slate dark:text-white/60">{variable.description}</p>
                        </div>
                      ))
                    ) : (
                      <p className="px-2 py-1.5 text-sm text-slate dark:text-white/70">No template variables loaded.</p>
                    )}
                  </div>
                </div>
              </aside>
            </div>

            {notice && (
              <p
                role={notice.tone === 'error' ? 'alert' : 'status'}
                aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
                aria-atomic="true"
                className={`text-sm ${notice.tone === 'error' ? 'text-red-600' : 'text-emerald-700 dark:text-emerald-300'}`}
              >
                {notice.message}
              </p>
            )}
            {saveSmtp.isError && (
              <p role="alert" className="text-sm text-red-600">{resolveApiMessage(saveSmtp.error, 'Failed to save SMTP settings.')}</p>
            )}
            {testSmtp.isError && (
              <p role="alert" className="text-sm text-red-600">{resolveApiMessage(testSmtp.error, 'Failed to test SMTP settings.')}</p>
            )}
        </div>
      </section>

      {confirmDiscardUnsavedIntegrationChanges.discardDialog}
    </div>
  )
}

export function IntegrationsSettingsPage() {
  return <SMTPIntegrationSettingsPage />
}

function TextInput({
  id,
  label,
  value,
  onChange,
  error,
  type = 'text',
  placeholder,
  autoComplete,
  min,
  max,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  error?: string
  type?: string
  placeholder?: string
  autoComplete?: string
  min?: number
  max?: number
}) {
  const errorId = `${id}-error`
  return (
    <div>
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <input
        id={id}
        type={type}
        min={min}
        max={max}
        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
        value={value}
        placeholder={placeholder}
        autoComplete={autoComplete}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {error && (
        <p id={errorId} className="mt-1 text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  )
}

function TextArea({
  id,
  label,
  value,
  onChange,
  error,
  helperText,
  monospace = true,
  placeholder,
  rows = 6,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  error?: string
  helperText?: string
  monospace?: boolean
  placeholder?: string
  rows?: number
}) {
  const errorId = `${id}-error`
  const helperId = `${id}-helper`
  return (
    <div>
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <textarea
        id={id}
        rows={rows}
        className={`mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45 ${
          monospace ? 'font-mono' : ''
        }`}
        value={value}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={[error ? errorId : null, helperText ? helperId : null].filter(Boolean).join(' ') || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {helperText && (
        <p id={helperId} className="mt-1 text-xs text-slate dark:text-white/60">
          {helperText}
        </p>
      )}
      {error && (
        <p id={errorId} className="mt-1 text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-l border-slate/20 py-1 pl-3 dark:border-cyan-900/40">
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold">{value}</p>
    </div>
  )
}

function resolveTestValidationError(
  draft: SMTPSettingsDraft,
  sendTestEmail: boolean,
  testRecipient: string,
  firstValidationError: string | null,
) {
  if (firstValidationError) {
    return firstValidationError
  }
  if (!draft.host.trim()) {
    return 'SMTP host is required before testing.'
  }
  const recipient = testRecipient.trim()
  if (!sendTestEmail) {
    return null
  }
  if (!recipient) {
    return 'Recipient email is required before sending a test email.'
  }
  if (!looksLikeEmail(recipient)) {
    return 'Enter a valid test recipient email address.'
  }
  if (!draft.from_email.trim()) {
    return 'Sender email is required before sending a test email.'
  }
  return null
}

function looksLikeEmail(value: string) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)
}

function describeHealthStatus(settings: SMTPSettings) {
  if (settings.has_unreadable_secret) {
    return 'Secret issue'
  }
  if (!settings.configured) {
    return 'Not configured'
  }
  if (!settings.enabled) {
    return 'Disabled'
  }
  if (settings.health_status === 'healthy') {
    return 'Healthy'
  }
  if (settings.health_status === 'error') {
    return 'Error'
  }
  if (settings.health_status === 'warning') {
    return 'Warning'
  }
  return 'Unknown'
}

function healthBadgeClass(status: SMTPSettings['health_status']) {
  if (status === 'healthy') {
    return 'tl-chip-success'
  }
  if (status === 'warning') {
    return 'tl-chip-warning'
  }
  if (status === 'error') {
    return 'tl-chip-danger'
  }
  return 'tl-chip-neutral'
}

function resolveApiMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return error.message
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return fallback
}
