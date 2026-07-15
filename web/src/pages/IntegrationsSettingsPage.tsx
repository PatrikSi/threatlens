import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { formatDateTime } from '../utils/datetime'
import {
  Feed,
  IntegrationDeliveryReplayResponse,
  NotificationEventType,
  NotificationTemplateVariable,
  SMTPAnalyticsResponse,
  SMTPDelivery,
  SMTPDeliveryListResponse,
  SMTPHook,
  SMTPHookTestRequest,
  SMTPHookWriteRequest,
  SMTPSecurityMode,
  SMTPTemplateDefault,
  SMTPTestResponse,
} from '../types/api'
import {
  applySMTPTemplateDefault,
  createSMTPHookDraft,
  createSMTPHookRequest,
  DEFAULT_SMTP_HOOK_DRAFT,
  getFirstSMTPHookDraftValidationError,
  smtpHookDraftFingerprint,
  SMTPHookDraft,
  validateSMTPHookDraft,
} from './smtpHookDraft'

type NoticeState = {
  tone: 'success' | 'error'
  message: string
}

type SendForValue = NotificationEventType | 'all' | 'custom'

const DELIVERY_HISTORY_REFRESH_MS = 30_000
const EMPTY_TEMPLATE_DEFAULTS: SMTPTemplateDefault[] = []
const ALL_EVENT_TYPES: NotificationEventType[] = [
  'rss_item_new',
  'alert_match',
  'feed_failing',
  'webhook_failed',
  'daily_digest',
]
const SMTP_EVENT_OPTIONS: Array<{ value: NotificationEventType; label: string; description: string }> = [
  { value: 'rss_item_new', label: 'New RSS Item', description: 'Email each new item received from the selected feeds.' },
  { value: 'alert_match', label: 'Alert Match', description: 'Email when an item matches one or more alert interests.' },
  { value: 'feed_failing', label: 'Feed Failing', description: 'Email when a feed reaches the repeated-failure threshold.' },
  { value: 'webhook_failed', label: 'Webhook Failed', description: 'Email when a webhook delivery reaches a terminal failure.' },
  { value: 'daily_digest', label: 'Daily Digest', description: 'Email the daily summary for the selected feed scope.' },
]

export function SMTPIntegrationSettingsPage() {
  const queryClient = useQueryClient()
  const [selectedHookId, setSelectedHookId] = useState<string | null>(null)
  const [selectionInitialized, setSelectionInitialized] = useState(false)
  const [draft, setDraftState] = useState<SMTPHookDraft>(DEFAULT_SMTP_HOOK_DRAFT)
  const [hasUserEdited, setHasUserEdited] = useState(false)
  const [notice, setNotice] = useState<NoticeState | null>(null)
  const [sendTestEmail, setSendTestEmail] = useState(false)
  const [testRecipient, setTestRecipient] = useState('')
  const [testResult, setTestResult] = useState<SMTPTestResponse | null>(null)
  const [pendingDelete, setPendingDelete] = useState<SMTPHook | null>(null)
  const [pendingReplay, setPendingReplay] = useState<SMTPDelivery | null>(null)

  const hooksQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'hooks'],
    queryFn: () => apiFetch<SMTPHook[]>('/integrations/smtp/hooks'),
  })
  const analyticsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'analytics'],
    queryFn: () => apiFetch<SMTPAnalyticsResponse>('/integrations/smtp/analytics'),
    refetchInterval: DELIVERY_HISTORY_REFRESH_MS,
  })
  const defaultsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'template-defaults'],
    queryFn: () => apiFetch<SMTPTemplateDefault[]>('/integrations/smtp/template-defaults'),
  })
  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })
  const variablesQuery = useQuery({
    queryKey: ['notifications', 'template-variables'],
    queryFn: () => apiFetch<NotificationTemplateVariable[]>('/notifications/template-variables'),
  })

  const hooks = hooksQuery.data ?? []
  const feeds = feedsQuery.data ?? []
  const variables = variablesQuery.data ?? []
  const templateDefaults = defaultsQuery.data ?? EMPTY_TEMPLATE_DEFAULTS
  const selectedHook = hooks.find((hook) => hook.id === selectedHookId) ?? null
  const newHookDraft = useMemo(() => createNewHookDraft(templateDefaults), [templateDefaults])
  const baselineDraft = useMemo(
    () => (selectedHook ? createSMTPHookDraft(selectedHook) : newHookDraft),
    [newHookDraft, selectedHook],
  )
  const validation = useMemo(() => validateSMTPHookDraft(draft), [draft])
  const firstValidationError = getFirstSMTPHookDraftValidationError(validation)
  const draftDirty = smtpHookDraftFingerprint(draft) !== smtpHookDraftFingerprint(baselineDraft)
  const confirmDiscardUnsavedChanges = useUnsavedChangesWarning(
    draftDirty,
    'Discard unsaved SMTP hook changes?',
  )
  const credentialSources = hooks.filter(
    (hook) => hook.id !== selectedHookId && !hook.uses_shared_credentials && Boolean(hook.host),
  )
  const selectedCredentialSource = hooks.find((hook) => hook.id === draft.credential_source_id) ?? null
  const currentSendFor = resolveSendForValue(draft.event_types)

  const setDraft: Dispatch<SetStateAction<SMTPHookDraft>> = (value) => {
    setHasUserEdited(true)
    setTestResult(null)
    setDraftState(value)
  }

  useEffect(() => {
    if (selectionInitialized || !hooksQuery.data) {
      return
    }
    const firstHook = hooksQuery.data[0]
    setSelectionInitialized(true)
    if (firstHook) {
      setSelectedHookId(firstHook.id)
      setDraftState(createSMTPHookDraft(firstHook))
    } else {
      setDraftState(createNewHookDraft(templateDefaults))
    }
  }, [hooksQuery.data, selectionInitialized, templateDefaults])

  useEffect(() => {
    if (hasUserEdited) {
      return
    }
    if (selectedHook) {
      setDraftState(createSMTPHookDraft(selectedHook))
    } else if (selectionInitialized) {
      setDraftState(newHookDraft)
    }
  }, [hasUserEdited, newHookDraft, selectedHook, selectionInitialized])

  const saveHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'save'],
    mutationFn: ({ hookId, hook }: { hookId: string | null; hook: SMTPHookWriteRequest }) =>
      apiFetch<SMTPHook>(hookId ? `/integrations/smtp/hooks/${hookId}` : '/integrations/smtp/hooks', {
        method: hookId ? 'PATCH' : 'POST',
        body: JSON.stringify(hook),
      }),
    onSuccess: (saved, variables) => {
      setSelectedHookId(saved.id)
      setDraftState(createSMTPHookDraft(saved))
      setHasUserEdited(false)
      setTestResult(null)
      setNotice({ tone: 'success', message: variables.hookId ? 'SMTP hook updated.' : 'SMTP hook created.' })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp'] })
      void queryClient.invalidateQueries({ queryKey: ['integrations'] })
    },
  })

  const deleteHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'delete'],
    mutationFn: (hookId: string) => apiFetch<void>(`/integrations/smtp/hooks/${hookId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setPendingDelete(null)
      setSelectedHookId(null)
      setDraftState(newHookDraft)
      setHasUserEdited(false)
      setTestResult(null)
      setNotice({ tone: 'success', message: 'SMTP hook deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp'] })
      void queryClient.invalidateQueries({ queryKey: ['integrations'] })
    },
    onError: () => setPendingDelete(null),
  })

  const testHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'test'],
    mutationFn: (payload: SMTPHookTestRequest) =>
      apiFetch<SMTPTestResponse>('/integrations/smtp/hooks/test', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (result) => {
      setTestResult(result)
      setNotice({
        tone: result.success ? 'success' : 'error',
        message: result.success ? 'SMTP test succeeded.' : result.error || 'SMTP test failed.',
      })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp', 'hooks'] })
    },
  })

  const deliveriesQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'hooks', selectedHookId, 'deliveries'],
    queryFn: () =>
      apiFetch<SMTPDeliveryListResponse>(
        `/integrations/smtp/hooks/${selectedHookId}/deliveries?page=1&page_size=10`,
      ),
    enabled: Boolean(selectedHookId),
    refetchInterval: selectedHookId ? DELIVERY_HISTORY_REFRESH_MS : false,
  })

  const replayDelivery = useMutation({
    mutationKey: ['integrations', 'smtp', 'deliveries', 'replay'],
    mutationFn: ({ hookId, deliveryId }: { hookId: string; deliveryId: string }) =>
      apiFetch<IntegrationDeliveryReplayResponse>(
        `/integrations/smtp/hooks/${hookId}/deliveries/${deliveryId}/replay`,
        { method: 'POST' },
      ),
    onSuccess: (result) => {
      setPendingReplay(null)
      setNotice({
        tone: 'success',
        message: result.queued ? 'Dead-letter delivery queued for replay.' : 'Replay created and awaiting queue recovery.',
      })
      void queryClient.invalidateQueries({
        queryKey: ['integrations', 'smtp', 'hooks', selectedHookId, 'deliveries'],
      })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp', 'analytics'] })
    },
    onError: () => setPendingReplay(null),
  })

  const onSelectHook = (hook: SMTPHook) => {
    if (hook.id === selectedHookId) {
      return
    }
    confirmDiscardUnsavedChanges(() => {
      setSelectedHookId(hook.id)
      setDraftState(createSMTPHookDraft(hook))
      setHasUserEdited(false)
      resetTransientState()
    })
  }

  const onCreateHook = () => {
    confirmDiscardUnsavedChanges(() => {
      setSelectedHookId(null)
      setDraftState(newHookDraft)
      setHasUserEdited(false)
      resetTransientState()
    })
  }

  const resetTransientState = () => {
    setNotice(null)
    setTestResult(null)
    setSendTestEmail(false)
    setTestRecipient('')
    setPendingReplay(null)
    saveHook.reset()
    testHook.reset()
    replayDelivery.reset()
  }

  const onSave = () => {
    if (firstValidationError) {
      setNotice({ tone: 'error', message: firstValidationError })
      return
    }
    setNotice(null)
    saveHook.mutate({ hookId: selectedHookId, hook: createSMTPHookRequest(draft) })
  }

  const onTest = () => {
    const testError = resolveTestValidationError(draft, sendTestEmail, testRecipient, firstValidationError)
    if (testError) {
      setNotice({ tone: 'error', message: testError })
      return
    }
    setNotice(null)
    testHook.mutate({
      hook_id: selectedHookId,
      hook: !selectedHookId || draftDirty ? createSMTPHookRequest(draft) : null,
      send_email: sendTestEmail,
      recipient_email: sendTestEmail ? testRecipient.trim() : null,
    })
  }

  const onCredentialSourceChange = (sourceId: string) => {
    if (!sourceId) {
      setDraft((current) => ({
        ...current,
        credential_source_id: null,
        host: current.credential_source_id ? '' : current.host,
        port: current.credential_source_id ? '587' : current.port,
        security: current.credential_source_id ? 'starttls' : current.security,
        username: current.credential_source_id ? '' : current.username,
        password: '',
        clear_password: false,
      }))
      return
    }
    const source = hooks.find((hook) => hook.id === sourceId)
    if (!source) {
      setNotice({ tone: 'error', message: 'The selected credential source is no longer available.' })
      return
    }
    setDraft((current) => ({
      ...current,
      credential_source_id: source.id,
      host: source.host ?? '',
      port: String(source.port),
      security: source.security,
      username: source.username ?? '',
      password: '',
      clear_password: false,
    }))
  }

  const onSendForChange = (sendFor: SendForValue) => {
    if (sendFor === 'custom') {
      return
    }
    const template = templateDefaults.find((entry) => entry.send_for === sendFor)
    if (!template) {
      setNotice({ tone: 'error', message: 'The default template for this event could not be loaded.' })
      return
    }
    setDraft((current) => applySMTPTemplateDefault(current, template))
  }

  const loadError = hooksQuery.error ?? analyticsQuery.error ?? defaultsQuery.error ?? feedsQuery.error ?? variablesQuery.error

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
        <h2 className="mt-1 font-display text-xl">SMTP Notifications</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Route different notification events to independent email destinations while sharing relay credentials where appropriate.
        </p>
        {loadError && (
          <p role="alert" className="mt-3 text-sm text-red-600">
            {resolveApiMessage(loadError, 'Failed to load SMTP integrations.')}
          </p>
        )}
      </section>

      <SMTPAnalyticsPanel analytics={analyticsQuery.data} loading={analyticsQuery.isLoading} error={analyticsQuery.error} />

      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-display text-lg">Saved SMTP Hooks</h3>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40"
              onClick={onCreateHook}
            >
              New hook
            </button>
          </div>

          <div className="mt-3 max-h-[34rem] overflow-auto rounded-lg border border-slate/20 dark:border-cyan-900/40">
            {hooks.map((hook) => {
              const selected = hook.id === selectedHookId
              return (
                <button
                  key={hook.id}
                  type="button"
                  aria-pressed={selected}
                  className={`grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-slate/10 px-3 py-2.5 text-left text-sm transition last:border-b-0 dark:border-cyan-900/30 ${
                    selected ? 'bg-cyan/10 dark:bg-cyan-950/50' : 'hover:bg-slate/5 dark:hover:bg-white/[0.03]'
                  }`}
                  onClick={() => onSelectHook(hook)}
                >
                  <div className="min-w-0">
                    <div className="flex min-w-0 items-center gap-2">
                      <p className="truncate font-semibold">{hook.name}</p>
                      {hook.is_default && <span className="tl-chip tl-chip-neutral">Default</span>}
                    </div>
                    <p className="mt-0.5 truncate text-xs text-slate dark:text-white/65">
                      {describeSendFor(hook.event_types)} · {describeFeedScope(hook.feed_scope, hook.feed_ids.length)}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-slate dark:text-white/55">
                      {hook.to_emails.length} recipient{hook.to_emails.length === 1 ? '' : 's'}
                      {hook.uses_shared_credentials ? ` · Auth from ${hook.credential_source_name || 'another hook'}` : ''}
                    </p>
                  </div>
                  <span className={`tl-chip ${hook.enabled ? 'tl-chip-success' : 'tl-chip-neutral'}`}>
                    {hook.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </button>
              )
            })}
          </div>

          {hooksQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-white/70">Loading SMTP hooks...</p>}
          {hooksQuery.isError && (
            <p className="mt-3 text-sm text-red-600">{resolveApiMessage(hooksQuery.error, 'Failed to load SMTP hooks.')}</p>
          )}
          {!hooksQuery.isLoading && !hooks.length && (
            <p className="mt-3 rounded-lg border border-dashed border-slate/25 p-3 text-sm text-slate dark:border-cyan-900/40 dark:text-white/70">
              No SMTP hooks are configured. Create one to route a notification event to email.
            </p>
          )}
        </section>

        <div className="space-y-4">
          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-lg">{selectedHook ? 'Edit SMTP Hook' : 'Create SMTP Hook'}</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/75">{describeEventDescription(currentSendFor)}</p>
              </div>
              <label className="flex items-center gap-2 rounded-full border border-slate/20 px-3 py-1 text-sm dark:border-cyan-900/40">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
                />
                Enabled
              </label>
            </div>

            {selectedHook?.has_unreadable_secret && (
              <div role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200">
                {selectedHook.uses_shared_credentials
                  ? 'The shared SMTP password cannot be decrypted. Update the credential source or choose another source.'
                  : 'The saved SMTP password cannot be decrypted. Enter a new password or clear it before saving.'}
              </div>
            )}

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <TextInput
                id="smtp-hook-name"
                label="Name"
                value={draft.name}
                error={validation.name}
                placeholder="SOC alert email"
                onChange={(value) => setDraft((current) => ({ ...current, name: value }))}
              />
              <div>
                <label htmlFor="smtp-send-for" className="text-sm font-semibold">Send For</label>
                <select
                  id="smtp-send-for"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={currentSendFor}
                  onChange={(event) => onSendForChange(event.target.value as SendForValue)}
                >
                  {currentSendFor === 'custom' && <option value="custom">Multiple events (legacy)</option>}
                  {SMTP_EVENT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                  <option value="all">All notification events</option>
                </select>
                <p className="mt-1 text-xs text-slate dark:text-white/60">Changing this selection loads its default email template.</p>
              </div>
            </div>

            <div className="mt-5 border-t border-slate/20 pt-5 dark:border-cyan-900/40">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="md:col-span-2">
                  <label htmlFor="smtp-credential-source" className="text-sm font-semibold">SMTP Authentication</label>
                  <select
                    id="smtp-credential-source"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.credential_source_id ?? ''}
                    onChange={(event) => onCredentialSourceChange(event.target.value)}
                  >
                    <option value="">Use credentials configured on this hook</option>
                    {credentialSources.map((source) => (
                      <option key={source.id} value={source.id}>Reuse credentials from {source.name}</option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-slate dark:text-white/60">
                    Shared authentication follows password changes made on the source hook without copying the saved secret.
                  </p>
                </div>

                {draft.credential_source_id ? (
                  <div className="md:col-span-2 rounded-lg border border-slate/20 px-3 py-3 dark:border-cyan-900/40">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-semibold">Shared transport</p>
                      <span className="tl-chip tl-chip-neutral">{selectedCredentialSource?.name ?? 'Credential source'}</span>
                    </div>
                    <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
                      <div><dt className="text-xs text-slate dark:text-white/55">Host</dt><dd className="mt-1 font-semibold">{draft.host || 'Not configured'}</dd></div>
                      <div><dt className="text-xs text-slate dark:text-white/55">Port / Security</dt><dd className="mt-1 font-semibold">{draft.port} · {describeSecurity(draft.security)}</dd></div>
                      <div><dt className="text-xs text-slate dark:text-white/55">Username</dt><dd className="mt-1 truncate font-semibold">{draft.username || 'No authentication'}</dd></div>
                    </dl>
                  </div>
                ) : (
                  <>
                    <TextInput id="smtp-host" label="Host" value={draft.host} error={validation.host} placeholder="smtp.example.com" onChange={(value) => setDraft((current) => ({ ...current, host: value }))} />
                    <TextInput id="smtp-port" label="Port" type="number" min={1} max={65535} value={draft.port} error={validation.port} onChange={(value) => setDraft((current) => ({ ...current, port: value }))} />
                    <div>
                      <label htmlFor="smtp-security" className="text-sm font-semibold">Security</label>
                      <select id="smtp-security" className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]" value={draft.security} onChange={(event) => setDraft((current) => ({ ...current, security: event.target.value as SMTPSecurityMode }))}>
                        <option value="starttls">STARTTLS</option>
                        <option value="ssl_tls">SSL/TLS</option>
                        <option value="none">None</option>
                      </select>
                    </div>
                    <TextInput id="smtp-timeout" label="Timeout (seconds)" type="number" min={1} max={60} value={draft.timeout_seconds} error={validation.timeout_seconds} onChange={(value) => setDraft((current) => ({ ...current, timeout_seconds: value }))} />
                    <TextInput id="smtp-username" label="Username" value={draft.username} error={validation.username} autoComplete="username" onChange={(value) => setDraft((current) => ({ ...current, username: value }))} />
                    <TextInput
                      id="smtp-password"
                      label="Password"
                      type="password"
                      value={draft.password}
                      error={validation.password}
                      autoComplete="new-password"
                      placeholder={selectedHook?.password_configured && !selectedHook.uses_shared_credentials ? 'Saved password configured' : ''}
                      onChange={(value) => setDraft((current) => ({ ...current, password: value, clear_password: value ? false : current.clear_password }))}
                    />
                  </>
                )}

                <TextInput id="smtp-from-email" label="Sender Email" type="email" value={draft.from_email} error={validation.from_email} placeholder="threatlens@example.com" onChange={(value) => setDraft((current) => ({ ...current, from_email: value }))} />
                <TextInput id="smtp-from-name" label="Sender Name" value={draft.from_name} placeholder="ThreatLens" onChange={(value) => setDraft((current) => ({ ...current, from_name: value }))} />
                <div className="md:col-span-2">
                  <TextArea id="smtp-to-emails" label="Recipient Emails" value={draft.to_emails} error={validation.to_emails} rows={3} placeholder="analyst@example.com, soc@example.com" helperText="Separate addresses with commas, semicolons, or new lines." monospace={false} onChange={(value) => setDraft((current) => ({ ...current, to_emails: value }))} />
                </div>
              </div>

              {!draft.credential_source_id && selectedHook?.password_configured && !selectedHook.uses_shared_credentials && (
                <label className="mt-4 flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={draft.clear_password} disabled={Boolean(draft.password.trim())} onChange={(event) => setDraft((current) => ({ ...current, clear_password: event.target.checked }))} />
                  Clear saved password on save
                </label>
              )}
            </div>

            <div className="mt-5 border-t border-slate/20 pt-5 dark:border-cyan-900/40">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h4 className="font-semibold">Feed Scope</h4>
                  <p className="mt-1 text-xs text-slate dark:text-white/65">Limit this hook to all feeds or a selected set.</p>
                </div>
                <div role="group" aria-label="SMTP feed scope" className="flex rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40">
                  <button type="button" aria-pressed={draft.feed_scope === 'all'} className={`rounded px-3 py-1 text-sm ${draft.feed_scope === 'all' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'}`} onClick={() => setDraft((current) => ({ ...current, feed_scope: 'all', feed_ids: [] }))}>Any feed</button>
                  <button type="button" aria-pressed={draft.feed_scope === 'selected'} className={`rounded px-3 py-1 text-sm ${draft.feed_scope === 'selected' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'}`} onClick={() => setDraft((current) => ({ ...current, feed_scope: 'selected' }))}>Selected feeds</button>
                </div>
              </div>
              {draft.feed_scope === 'selected' && (
                <div className="mt-4 grid max-h-64 gap-2 overflow-auto md:grid-cols-2">
                  {feeds.map((feed) => (
                    <label key={feed.id} className="flex items-start gap-3 rounded border border-slate/20 p-3 text-sm dark:border-cyan-900/40">
                      <input className="mt-1" type="checkbox" checked={draft.feed_ids.includes(feed.id)} onChange={() => setDraft((current) => ({ ...current, feed_ids: toggleValue(current.feed_ids, feed.id) }))} />
                      <span className="min-w-0"><span className="block truncate font-semibold">{feed.name}</span><span className="block truncate text-xs text-slate dark:text-white/60">{feed.url}</span></span>
                    </label>
                  ))}
                  {!feeds.length && <p className="text-sm text-slate dark:text-white/70">No feeds are available.</p>}
                </div>
              )}
              {validation.feed_ids && <p className="mt-1 text-xs text-red-600">{validation.feed_ids}</p>}
            </div>

            <div className="mt-5 border-t border-slate/20 pt-5 dark:border-cyan-900/40">
              <div className="grid gap-4">
                <TextInput id="smtp-subject-template" label="Email Subject Template" value={draft.subject_template} error={validation.subject_template} onChange={(value) => setDraft((current) => ({ ...current, subject_template: value }))} />
                <TextArea id="smtp-html-template" label="Email HTML Template" value={draft.html_template} error={validation.html_template} rows={10} onChange={(value) => setDraft((current) => ({ ...current, html_template: value }))} />
              </div>
              <details className="mt-4 rounded-lg border border-slate/20 px-3 py-2 dark:border-cyan-900/40">
                <summary className="cursor-pointer text-sm font-semibold">Template Variables</summary>
                <div className="mt-3 grid max-h-64 gap-2 overflow-auto md:grid-cols-2">
                  {variables.map((variable) => (
                    <div key={variable.key} className="min-w-0 text-xs">
                      <code className="font-semibold">{`{{ ${variable.key} }}`}</code>
                      <p className="mt-0.5 text-slate dark:text-white/60">{variable.description}</p>
                    </div>
                  ))}
                </div>
              </details>
            </div>

            <div className="mt-5 grid gap-4 border-t border-slate/20 pt-5 dark:border-cyan-900/40 lg:grid-cols-[minmax(0,1fr)_280px]">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]" disabled={saveHook.isPending} onClick={onSave}>{saveHook.isPending ? 'Saving...' : selectedHook ? 'Save hook' : 'Create hook'}</button>
                  <button type="button" className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40" disabled={testHook.isPending} onClick={onTest}>{testHook.isPending ? 'Testing...' : 'Test SMTP'}</button>
                  {selectedHook && !selectedHook.is_default && (
                    <button type="button" className="tl-button-danger rounded px-3 py-2 text-sm font-semibold" onClick={() => confirmDiscardUnsavedChanges(() => setPendingDelete(selectedHook))}>Delete hook</button>
                  )}
                </div>
                {selectedHook?.is_default && <p className="mt-2 text-xs text-slate dark:text-white/60">The default hook can be disabled but remains available to older clients.</p>}
              </div>

              <div className="border-t border-slate/20 pt-4 dark:border-cyan-900/40 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
                <p className="text-sm font-semibold">Test Delivery</p>
                <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={sendTestEmail} onChange={(event) => { setSendTestEmail(event.target.checked); setTestResult(null) }} />Send a test email</label>
                {sendTestEmail && <div className="mt-3"><TextInput id="smtp-test-recipient" label="Recipient Email" type="email" value={testRecipient} placeholder="analyst@example.com" onChange={setTestRecipient} /></div>}
                <p className="mt-2 text-xs text-slate dark:text-white/60">{sendTestEmail ? 'Uses the current template and unsaved form values.' : 'Checks connection and authentication only.'}</p>
              </div>
            </div>

            {(notice || saveHook.isError || testHook.isError || replayDelivery.isError) && (
              <p role={(notice?.tone === 'error' || saveHook.isError || testHook.isError || replayDelivery.isError) ? 'alert' : 'status'} className={`mt-4 text-sm ${(notice?.tone === 'error' || saveHook.isError || testHook.isError || replayDelivery.isError) ? 'text-red-600' : 'text-emerald-700 dark:text-emerald-300'}`}>
                {saveHook.isError
                  ? resolveApiMessage(saveHook.error, 'Failed to save SMTP hook.')
                  : testHook.isError
                    ? resolveApiMessage(testHook.error, 'Failed to test SMTP hook.')
                    : replayDelivery.isError
                      ? resolveApiMessage(replayDelivery.error, 'Failed to replay SMTP delivery.')
                      : notice?.message}
              </p>
            )}

            {testResult && (
              <div className="mt-4 rounded-lg border border-slate/20 px-3 py-3 text-sm dark:border-cyan-900/40">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`tl-chip ${testResult.success ? 'tl-chip-success' : 'tl-chip-danger'}`}>{testResult.success ? 'Success' : 'Failed'}</span>
                  <span>{testResult.action === 'send' ? 'Test email' : 'Connection test'}</span>
                  <span className="text-slate dark:text-white/60">{testResult.duration_ms != null ? `${testResult.duration_ms} ms` : 'No duration'}</span>
                </div>
                {testResult.server_message && <code className="mt-2 block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{testResult.server_message}</code>}
                {testResult.error && <p className="mt-2 text-red-600">{testResult.error}</p>}
              </div>
            )}
          </section>

          <SMTPDeliveryHistory
            hook={selectedHook}
            feeds={feeds}
            deliveries={deliveriesQuery.data}
            loading={deliveriesQuery.isLoading}
            error={deliveriesQuery.error}
            replaying={replayDelivery.isPending}
            onReplay={setPendingReplay}
          />
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete SMTP hook?"
        description={pendingDelete ? `Delete ${pendingDelete.name}? Delivery history will remain in retained integration records.` : undefined}
        confirmLabel="Delete hook"
        isConfirming={deleteHook.isPending}
        onConfirm={() => pendingDelete && deleteHook.mutate(pendingDelete.id)}
        onCancel={() => setPendingDelete(null)}
      />
      <ConfirmDialog
        open={Boolean(pendingReplay)}
        title="Replay dead-letter delivery?"
        description="This creates a new delivery using the hook's current credentials, recipients, and template."
        confirmLabel="Replay delivery"
        confirmTone="primary"
        isConfirming={replayDelivery.isPending}
        onConfirm={() => pendingReplay && selectedHookId && replayDelivery.mutate({ hookId: selectedHookId, deliveryId: pendingReplay.id })}
        onCancel={() => setPendingReplay(null)}
      />
      {confirmDiscardUnsavedChanges.discardDialog}
    </div>
  )
}

export function IntegrationsSettingsPage() {
  return <SMTPIntegrationSettingsPage />
}

function SMTPAnalyticsPanel({ analytics, loading, error }: { analytics?: SMTPAnalyticsResponse; loading: boolean; error: unknown }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h3 className="font-display text-lg">SMTP Analytics</h3><p className="mt-1 text-sm text-slate dark:text-white/75">Delivery health across all active and retained SMTP hooks.</p></div>
        {loading && <span className="text-sm text-slate dark:text-white/70">Loading analytics...</span>}
      </div>
      {Boolean(error) && <p className="mt-3 text-sm text-red-600">{resolveApiMessage(error, 'Failed to load SMTP analytics.')}</p>}
      {analytics && (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <Metric label="Enabled Hooks" value={`${analytics.enabled_hook_count} / ${analytics.hook_count}`} />
            <Metric label="Total Deliveries" value={String(analytics.total_deliveries)} />
            <Metric label="Success Rate" value={`${analytics.success_rate_pct.toFixed(1)}%`} />
            <Metric label="Failures 24h" value={String(analytics.failures_last_24h)} />
            <Metric label="Queued / Retry" value={`${analytics.pending_deliveries} / ${analytics.retry_wait_deliveries}`} />
          </div>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
            <div className="rounded-lg border border-slate/20 px-3 py-3 dark:border-cyan-900/40">
              <p className="text-sm font-semibold">Event Breakdown</p>
              {analytics.events.length ? (
                <div className="mt-2 grid gap-x-5 gap-y-2 sm:grid-cols-2">
                  {analytics.events.map((event) => (
                    <div key={event.event_type} className="flex items-center justify-between gap-3 text-sm">
                      <span>{describeEventType(event.event_type)}</span>
                      <span className="font-semibold">{event.failed_deliveries} / {event.total_deliveries} failed</span>
                    </div>
                  ))}
                </div>
              ) : <p className="mt-2 text-sm text-slate dark:text-white/70">No SMTP deliveries recorded yet.</p>}
            </div>
            <div className="rounded-lg border border-slate/20 px-3 py-3 dark:border-cyan-900/40">
              <p className="text-sm font-semibold">Most Failing Hook</p>
              <p className="mt-2 truncate text-sm font-semibold">{analytics.most_failing_hook?.hook_name ?? 'None'}</p>
              <p className="mt-1 text-xs text-slate dark:text-white/60">{analytics.most_failing_hook ? `${analytics.most_failing_hook.failed_deliveries} retained failures` : 'No terminal failures recorded.'}</p>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function SMTPDeliveryHistory({ hook, feeds, deliveries, loading, error, replaying, onReplay }: { hook: SMTPHook | null; feeds: Feed[]; deliveries?: SMTPDeliveryListResponse; loading: boolean; error: unknown; replaying: boolean; onReplay: (delivery: SMTPDelivery) => void }) {
  const latest = deliveries?.deliveries[0]
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h3 className="font-display text-lg">Delivery History</h3><p className="mt-1 text-sm text-slate dark:text-white/75">Inspect generic delivery state, retry attempts, and SMTP outcomes for this hook.</p></div>
        {latest && <span className={`tl-chip ${deliveryStateBadgeClass(latest.state)}`}>Last status: {describeDeliveryState(latest.state)}</span>}
      </div>
      {!hook && <p className="mt-3 text-sm text-slate dark:text-white/70">Select a saved SMTP hook to view delivery history.</p>}
      {hook && loading && <p className="mt-3 text-sm text-slate dark:text-white/70">Loading delivery history...</p>}
      {hook && Boolean(error) && <p className="mt-3 text-sm text-red-600">{resolveApiMessage(error, 'Failed to load SMTP delivery history.')}</p>}
      {hook && deliveries?.deliveries.length ? (
        <div className="mt-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="Deliveries" value={String(deliveries.total)} />
            <Metric label="Last Attempts" value={String(latest?.attempt_count ?? 0)} />
            <Metric label="Last Duration" value={latest?.last_duration_ms != null ? `${latest.last_duration_ms} ms` : 'n/a'} />
            <Metric label="Last Updated" value={latest ? formatDateTime(latest.updated_at) : 'Never'} />
          </div>
          {deliveries.deliveries.map((delivery) => {
            const feedName = feeds.find((feed) => feed.id === delivery.feed_id)?.name
            return (
              <details key={delivery.id} className="rounded-lg border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
                <summary className="cursor-pointer list-none">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`tl-chip ${deliveryStateBadgeClass(delivery.state)}`}>{describeDeliveryState(delivery.state)}</span>
                        <span className="tl-chip tl-chip-neutral">{delivery.delivery_kind === 'replay' ? 'Replay' : 'Live'}</span>
                        <span className="tl-chip tl-chip-neutral">{describeEventType(delivery.event_type)}</span>
                      </div>
                      <p className="mt-2 text-sm font-semibold">{feedName || (delivery.feed_id ? `Feed ${delivery.feed_id.slice(0, 8)}` : 'SMTP delivery')}</p>
                      <p className="mt-1 text-xs text-slate dark:text-white/60">Created {formatDateTime(delivery.created_at)}</p>
                    </div>
                    <div className="text-right text-xs text-slate dark:text-white/60"><p>{delivery.last_duration_ms != null ? `${delivery.last_duration_ms} ms` : 'No duration'}</p><p>{delivery.attempt_count} of {delivery.max_attempts} attempts</p></div>
                  </div>
                </summary>
                <div className="mt-4 space-y-3 text-sm">
                  {delivery.state === 'dead_letter' && <button type="button" className="rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40" disabled={replaying} onClick={() => onReplay(delivery)}>Replay dead letter</button>}
                  {delivery.last_error_message && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-red-700 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200"><p className="font-semibold">{delivery.last_error_code || 'Delivery error'}</p><p className="mt-1 break-words text-xs">{delivery.last_error_message}</p></div>}
                  <div>
                    <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Attempts</p>
                    {delivery.attempts.length ? <div className="mt-2 space-y-2">{delivery.attempts.map((attempt) => <div key={attempt.attempt_number} className="grid gap-1 rounded bg-slate/5 px-3 py-2 text-xs dark:bg-white/5 sm:grid-cols-[80px_1fr_auto]"><span className="font-semibold">Attempt {attempt.attempt_number}</span><span>{attempt.error_message || `${attempt.accepted_count ?? 0} of ${attempt.recipient_count ?? 0} recipients accepted`}</span><span>{attempt.duration_ms != null ? `${attempt.duration_ms} ms` : attempt.status}</span></div>)}</div> : <p className="mt-2 text-xs text-slate dark:text-white/60">No worker attempt has started yet.</p>}
                  </div>
                </div>
              </details>
            )
          })}
        </div>
      ) : hook && !loading && !error ? <p className="mt-3 text-sm text-slate dark:text-white/70">No deliveries have been recorded for this hook.</p> : null}
    </section>
  )
}

function TextInput({ id, label, value, onChange, error, type = 'text', placeholder, autoComplete, min, max, disabled = false }: { id: string; label: string; value: string; onChange: (value: string) => void; error?: string; type?: string; placeholder?: string; autoComplete?: string; min?: number; max?: number; disabled?: boolean }) {
  const errorId = `${id}-error`
  return <div><label htmlFor={id} className="text-sm font-semibold">{label}</label><input id={id} type={type} min={min} max={max} disabled={disabled} className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45" value={value} placeholder={placeholder} autoComplete={autoComplete} aria-invalid={error ? true : undefined} aria-describedby={error ? errorId : undefined} onChange={(event) => onChange(event.target.value)} />{error && <p id={errorId} className="mt-1 text-xs text-red-600">{error}</p>}</div>
}

function TextArea({ id, label, value, onChange, error, helperText, monospace = true, placeholder, rows = 6 }: { id: string; label: string; value: string; onChange: (value: string) => void; error?: string; helperText?: string; monospace?: boolean; placeholder?: string; rows?: number }) {
  const errorId = `${id}-error`
  const helperId = `${id}-helper`
  return <div><label htmlFor={id} className="text-sm font-semibold">{label}</label><textarea id={id} rows={rows} className={`mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019] ${monospace ? 'font-mono' : ''}`} value={value} placeholder={placeholder} aria-invalid={error ? true : undefined} aria-describedby={[error ? errorId : null, helperText ? helperId : null].filter(Boolean).join(' ') || undefined} onChange={(event) => onChange(event.target.value)} />{helperText && <p id={helperId} className="mt-1 text-xs text-slate dark:text-white/60">{helperText}</p>}{error && <p id={errorId} className="mt-1 text-xs text-red-600">{error}</p>}</div>
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 border-l border-slate/20 py-1 pl-3 dark:border-cyan-900/40"><p className="text-xs font-semibold uppercase text-slate dark:text-white/55">{label}</p><p className="mt-1 break-words text-sm font-semibold">{value}</p></div>
}

function createNewHookDraft(defaults: SMTPTemplateDefault[]): SMTPHookDraft {
  const template = defaults.find((entry) => entry.send_for === 'rss_item_new')
  return template ? applySMTPTemplateDefault(DEFAULT_SMTP_HOOK_DRAFT, template) : { ...DEFAULT_SMTP_HOOK_DRAFT }
}

function resolveSendForValue(eventTypes: NotificationEventType[]): SendForValue {
  if (eventTypes.length === 1) {
    return eventTypes[0]
  }
  const selected = new Set(eventTypes)
  if (ALL_EVENT_TYPES.every((eventType) => selected.has(eventType)) && selected.size === ALL_EVENT_TYPES.length) {
    return 'all'
  }
  return 'custom'
}

function resolveTestValidationError(draft: SMTPHookDraft, sendTestEmail: boolean, testRecipient: string, firstValidationError: string | null) {
  if (firstValidationError) return firstValidationError
  if (!draft.host.trim()) return draft.credential_source_id ? 'The selected credential source does not have an SMTP host.' : 'SMTP host is required before testing.'
  if (!sendTestEmail) return null
  const recipient = testRecipient.trim()
  if (!recipient) return 'Recipient email is required before sending a test email.'
  if (!looksLikeEmail(recipient)) return 'Enter a valid test recipient email address.'
  if (!draft.from_email.trim()) return 'Sender email is required before sending a test email.'
  return null
}

function toggleValue(values: string[], value: string) {
  return values.includes(value) ? values.filter((candidate) => candidate !== value) : [...values, value]
}

function describeSendFor(eventTypes: NotificationEventType[]) {
  const value = resolveSendForValue(eventTypes)
  if (value === 'all') return 'All notification events'
  if (value === 'custom') return `${eventTypes.length} notification events`
  return describeEventType(value)
}

function describeEventType(eventType: NotificationEventType) {
  return SMTP_EVENT_OPTIONS.find((option) => option.value === eventType)?.label ?? eventType
}

function describeEventDescription(value: SendForValue) {
  if (value === 'all') return 'Send this email template for every supported notification event.'
  if (value === 'custom') return 'This upgraded hook retains its existing multi-event selection until you choose a new option.'
  return SMTP_EVENT_OPTIONS.find((option) => option.value === value)?.description ?? 'Configure the event that sends this email.'
}

function describeFeedScope(scope: 'all' | 'selected', count: number) {
  return scope === 'all' ? 'Any feed' : `${count} selected feed${count === 1 ? '' : 's'}`
}

function describeSecurity(security: SMTPSecurityMode) {
  if (security === 'ssl_tls') return 'SSL/TLS'
  if (security === 'none') return 'None'
  return 'STARTTLS'
}

function describeDeliveryState(state: SMTPDelivery['state']) {
  if (state === 'retry_wait') return 'Retry scheduled'
  if (state === 'dead_letter') return 'Dead letter'
  if (state === 'sending') return 'Sending'
  if (state === 'pending') return 'Pending'
  if (state === 'succeeded') return 'Succeeded'
  return 'Failed'
}

function deliveryStateBadgeClass(state: SMTPDelivery['state']) {
  if (state === 'succeeded') return 'tl-chip-success'
  if (state === 'failed' || state === 'dead_letter') return 'tl-chip-danger'
  if (state === 'retry_wait') return 'tl-chip-warning'
  return 'tl-chip-neutral'
}

function looksLikeEmail(value: string) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)
}

function resolveApiMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) return error.message
  if (error instanceof Error && error.message.trim()) return error.message
  return fallback
}
