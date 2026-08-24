import { resolveApiErrorMessage } from '../api/errors'
import { NotificationTemplateVariable, SMTPHook, SMTPSecurityMode } from '../types/api'
import { SMTPHookDraft, SMTPHookDraftValidation } from './smtpHookDraft'
import {
  describeEventDescription,
  describeFeedScope,
  describeSecurity,
  describeSendFor,
  SendForValue,
  toggleValue,
} from './smtpIntegrationPresentation'
import { SMTPIntegrationController } from './useSMTPIntegrationController'

export function SMTPHookList({ controller }: { controller: SMTPIntegrationController }) {
  const { hooks, hooksQuery, onCreateHook, onSelectHook, selectedHookId } = controller
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-display text-lg">Saved SMTP Hooks</h3>
        <button type="button" className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40" onClick={onCreateHook}>New hook</button>
      </div>
      <div className="mt-3 max-h-[34rem] overflow-auto rounded-lg border border-slate/20 dark:border-cyan-900/40">
        {hooks.map((hook) => (
          <SMTPHookListItem
            key={hook.id}
            hook={hook}
            selected={hook.id === selectedHookId}
            onSelect={onSelectHook}
          />
        ))}
      </div>
      {hooksQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-white/70">Loading SMTP hooks...</p>}
      {hooksQuery.isError && (
        <p className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(hooksQuery.error, 'Failed to load SMTP hooks.')}
        </p>
      )}
      {!hooksQuery.isLoading && !hooks.length && (
        <p className="mt-3 rounded-lg border border-dashed border-slate/25 p-3 text-sm text-slate dark:border-cyan-900/40 dark:text-white/70">
          No SMTP hooks are configured. Create one to route a notification event to email.
        </p>
      )}
    </section>
  )
}

function SMTPHookListItem({
  hook,
  selected,
  onSelect,
}: {
  hook: SMTPHook
  selected: boolean
  onSelect: (hook: SMTPHook) => void
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      className={`grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-slate/10 px-3 py-2.5 text-left text-sm transition last:border-b-0 dark:border-cyan-900/30 ${
        selected ? 'bg-cyan/10 dark:bg-cyan-950/50' : 'hover:bg-slate/5 dark:hover:bg-white/[0.03]'
      }`}
      onClick={() => onSelect(hook)}
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
}

export function SMTPHookEditor({ controller }: { controller: SMTPIntegrationController }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <SMTPHookEditorHeading controller={controller} />
      <UnreadableSecretNotice hook={controller.selectedHook} />
      <SMTPEventFields controller={controller} />
      <SMTPTransportFields controller={controller} />
      <SMTPFeedScope controller={controller} />
      <SMTPTemplateFields
        draft={controller.draft}
        validation={controller.validation}
        variables={controller.variables}
        setDraft={controller.setDraft}
      />
      <SMTPHookActions controller={controller} />
      <SMTPHookStatus controller={controller} />
    </section>
  )
}

function SMTPHookEditorHeading({ controller }: { controller: SMTPIntegrationController }) {
  const { currentSendFor } = controller.eventAvailability
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h3 className="font-display text-lg">{controller.selectedHook ? 'Edit SMTP Hook' : 'Create SMTP Hook'}</h3>
        <p className="mt-1 text-sm text-slate dark:text-white/75">{describeEventDescription(currentSendFor)}</p>
      </div>
      <label className="flex items-center gap-2 rounded-full border border-slate/20 px-3 py-1 text-sm dark:border-cyan-900/40">
        <input
          type="checkbox"
          checked={controller.draft.enabled}
          onChange={(event) => controller.setDraft((current) => ({ ...current, enabled: event.target.checked }))}
        />
        Enabled
      </label>
    </div>
  )
}

function UnreadableSecretNotice({ hook }: { hook: SMTPHook | null }) {
  if (!hook?.has_unreadable_secret) return null
  return (
    <div role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200">
      {hook.uses_shared_credentials
        ? 'The shared SMTP password cannot be decrypted. Update the credential source or choose another source.'
        : 'The saved SMTP password cannot be decrypted. Enter a new password or clear it before saving.'}
    </div>
  )
}

function SMTPEventFields({ controller }: { controller: SMTPIntegrationController }) {
  const { availableEventOptions, currentSendFor, unavailableDailyBriefSelected, unavailableReportSelected } = controller.eventAvailability
  return (
    <div className="mt-4 grid gap-4 md:grid-cols-2">
      <TextInput
        id="smtp-hook-name"
        label="Name"
        value={controller.draft.name}
        error={controller.validation.name}
        placeholder="SOC alert email"
        onChange={(value) => controller.setDraft((current) => ({ ...current, name: value }))}
      />
      <div>
        <label htmlFor="smtp-send-for" className="text-sm font-semibold">Send For</label>
        <select
          id="smtp-send-for"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          value={currentSendFor}
          onChange={(event) => controller.onSendForChange(event.target.value as SendForValue)}
        >
          {currentSendFor === 'custom' && <option value="custom">Multiple events (legacy)</option>}
          {availableEventOptions.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
          <option value="all">All notification events</option>
        </select>
        <p className="mt-1 text-xs text-slate dark:text-white/60">Changing this selection loads its default email template.</p>
        <UnavailableAIEventNotice visible={unavailableDailyBriefSelected} feature="AI Daily Brief generation" />
        <UnavailableAIEventNotice visible={unavailableReportSelected} feature="AI reporting" />
      </div>
    </div>
  )
}

function UnavailableAIEventNotice({ visible, feature }: { visible: boolean; feature: string }) {
  if (!visible) return null
  return (
    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
      This existing selection is inactive until {feature} is enabled and configured.
    </p>
  )
}

function SMTPTransportFields({ controller }: { controller: SMTPIntegrationController }) {
  const { credentialSources, draft, selectedCredentialSource, selectedHook, setDraft, validation } = controller
  return (
    <div className="mt-5 border-t border-slate/20 pt-5 dark:border-cyan-900/40">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <label htmlFor="smtp-credential-source" className="text-sm font-semibold">SMTP Authentication</label>
          <select
            id="smtp-credential-source"
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.credential_source_id ?? ''}
            onChange={(event) => controller.onCredentialSourceChange(event.target.value)}
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
        {draft.credential_source_id
          ? <SharedTransport draft={draft} sourceName={selectedCredentialSource?.name} />
          : <OwnedTransportFields controller={controller} />}
        <TextInput id="smtp-from-email" label="Sender Email" type="email" value={draft.from_email} error={validation.from_email} placeholder="threatlens@example.com" onChange={(value) => setDraft((current) => ({ ...current, from_email: value }))} />
        <TextInput id="smtp-from-name" label="Sender Name" value={draft.from_name} placeholder="ThreatLens" onChange={(value) => setDraft((current) => ({ ...current, from_name: value }))} />
        <div className="md:col-span-2">
          <TextArea
            id="smtp-to-emails"
            label="Recipient Emails"
            value={draft.to_emails}
            error={validation.to_emails}
            rows={3}
            placeholder="analyst@example.com, soc@example.com"
            helperText="Separate addresses with commas, semicolons, or new lines."
            monospace={false}
            onChange={(value) => setDraft((current) => ({ ...current, to_emails: value }))}
          />
        </div>
      </div>
      {!draft.credential_source_id && selectedHook?.password_configured && !selectedHook.uses_shared_credentials && (
        <label className="mt-4 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={draft.clear_password} disabled={Boolean(draft.password.trim())} onChange={(event) => setDraft((current) => ({ ...current, clear_password: event.target.checked }))} />
          Clear saved password on save
        </label>
      )}
    </div>
  )
}

function SharedTransport({ draft, sourceName }: { draft: SMTPHookDraft; sourceName?: string }) {
  return (
    <div className="md:col-span-2 rounded-lg border border-slate/20 px-3 py-3 dark:border-cyan-900/40">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold">Shared transport</p>
        <span className="tl-chip tl-chip-neutral">{sourceName ?? 'Credential source'}</span>
      </div>
      <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
        <div><dt className="text-xs text-slate dark:text-white/55">Host</dt><dd className="mt-1 font-semibold">{draft.host || 'Not configured'}</dd></div>
        <div><dt className="text-xs text-slate dark:text-white/55">Port / Security</dt><dd className="mt-1 font-semibold">{draft.port} · {describeSecurity(draft.security)}</dd></div>
        <div><dt className="text-xs text-slate dark:text-white/55">Username</dt><dd className="mt-1 truncate font-semibold">{draft.username || 'No authentication'}</dd></div>
      </dl>
    </div>
  )
}

function OwnedTransportFields({ controller }: { controller: SMTPIntegrationController }) {
  const { draft, selectedHook, setDraft, validation } = controller
  return (
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
  )
}

function SMTPFeedScope({ controller }: { controller: SMTPIntegrationController }) {
  const { draft, feeds, setDraft, validation } = controller
  return (
    <div className="mt-5 border-t border-slate/20 pt-5 dark:border-cyan-900/40">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h4 className="font-semibold">Feed Scope</h4>
          <p className="mt-1 text-xs text-slate dark:text-white/65">Limit this hook to all feeds or a selected set.</p>
        </div>
        <div role="group" aria-label="SMTP feed scope" className="flex rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40">
          <button
            type="button"
            aria-pressed={draft.feed_scope === 'all'}
            className={`rounded px-3 py-1 text-sm ${draft.feed_scope === 'all' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'}`}
            onClick={() => setDraft((current) => ({ ...current, feed_scope: 'all', feed_ids: [] }))}
          >
            Any feed
          </button>
          <button
            type="button"
            aria-pressed={draft.feed_scope === 'selected'}
            className={`rounded px-3 py-1 text-sm ${draft.feed_scope === 'selected' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'}`}
            onClick={() => setDraft((current) => ({ ...current, feed_scope: 'selected' }))}
          >
            Selected feeds
          </button>
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
  )
}

function SMTPTemplateFields({
  draft,
  validation,
  variables,
  setDraft,
}: {
  draft: SMTPHookDraft
  validation: SMTPHookDraftValidation
  variables: NotificationTemplateVariable[]
  setDraft: SMTPIntegrationController['setDraft']
}) {
  return (
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
  )
}

function SMTPHookActions({ controller }: { controller: SMTPIntegrationController }) {
  const { selectedHook } = controller
  return (
    <div className="mt-5 grid gap-4 border-t border-slate/20 pt-5 dark:border-cyan-900/40 lg:grid-cols-[minmax(0,1fr)_280px]">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
            disabled={controller.saveHook.isPending}
            onClick={controller.onSave}
          >
            {controller.saveHook.isPending ? 'Saving...' : selectedHook ? 'Save hook' : 'Create hook'}
          </button>
          <button type="button" className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40" disabled={controller.testHook.isPending} onClick={controller.onTest}>{controller.testHook.isPending ? 'Testing...' : 'Test SMTP'}</button>
          {selectedHook && !selectedHook.is_default && (
            <button type="button" className="tl-button-danger rounded px-3 py-2 text-sm font-semibold" onClick={() => controller.confirmDiscardUnsavedChanges(() => { controller.setDeleteError(null); controller.setPendingDelete(selectedHook) })}>Delete hook</button>
          )}
        </div>
        {selectedHook?.is_default && <p className="mt-2 text-xs text-slate dark:text-white/60">The default hook can be disabled but remains available to older clients.</p>}
      </div>
      <div className="border-t border-slate/20 pt-4 dark:border-cyan-900/40 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
        <p className="text-sm font-semibold">Test Delivery</p>
        <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={controller.sendTestEmail} onChange={(event) => { controller.setSendTestEmail(event.target.checked); controller.setTestResult(null) }} />Send a test email</label>
        {controller.sendTestEmail && <div className="mt-3"><TextInput id="smtp-test-recipient" label="Recipient Email" type="email" value={controller.testRecipient} placeholder="analyst@example.com" onChange={controller.setTestRecipient} /></div>}
        <p className="mt-2 text-xs text-slate dark:text-white/60">{controller.sendTestEmail ? 'Uses the current template and unsaved form values.' : 'Checks connection and authentication only.'}</p>
      </div>
    </div>
  )
}

function SMTPHookStatus({ controller }: { controller: SMTPIntegrationController }) {
  const { notice, replayDelivery, saveHook, testHook, testResult } = controller
  const hasError = notice?.tone === 'error' || saveHook.isError || testHook.isError || replayDelivery.isError
  const message = saveHook.isError
    ? resolveApiErrorMessage(saveHook.error, 'Failed to save SMTP hook.')
    : testHook.isError
      ? resolveApiErrorMessage(testHook.error, 'Failed to test SMTP hook.')
      : replayDelivery.isError
        ? resolveApiErrorMessage(replayDelivery.error, 'Failed to replay SMTP delivery.')
        : notice?.message
  return (
    <>
      {(notice || saveHook.isError || testHook.isError || replayDelivery.isError) && (
        <p role={hasError ? 'alert' : 'status'} className={`mt-4 text-sm ${hasError ? 'text-red-600' : 'text-emerald-700 dark:text-emerald-300'}`}>{message}</p>
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
    </>
  )
}

type TextInputProps = {
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
  disabled?: boolean
}

function TextInput({ id, label, value, onChange, error, type = 'text', placeholder, autoComplete, min, max, disabled = false }: TextInputProps) {
  const errorId = `${id}-error`
  return (
    <div>
      <label htmlFor={id} className="text-sm font-semibold">{label}</label>
      <input
        id={id}
        type={type}
        min={min}
        max={max}
        disabled={disabled}
        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
        value={value}
        placeholder={placeholder}
        autoComplete={autoComplete}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {error && <p id={errorId} className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  )
}

type TextAreaProps = {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  error?: string
  helperText?: string
  monospace?: boolean
  placeholder?: string
  rows?: number
}

function TextArea({ id, label, value, onChange, error, helperText, monospace = true, placeholder, rows = 6 }: TextAreaProps) {
  const errorId = `${id}-error`
  const helperId = `${id}-helper`
  const describedBy = [error ? errorId : null, helperText ? helperId : null].filter(Boolean).join(' ') || undefined
  return (
    <div>
      <label htmlFor={id} className="text-sm font-semibold">{label}</label>
      <textarea
        id={id}
        rows={rows}
        className={`mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019] ${monospace ? 'font-mono' : ''}`}
        value={value}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      />
      {helperText && <p id={helperId} className="mt-1 text-xs text-slate dark:text-white/60">{helperText}</p>}
      {error && <p id={errorId} className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  )
}
