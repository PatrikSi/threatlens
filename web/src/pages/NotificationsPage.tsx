import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import {
  Feed,
  NotificationTemplateVariable,
  NotificationWebhook,
  NotificationWebhookField,
  NotificationWebhookTestResponse,
  NotificationWebhookWriteRequest,
} from '../types/api'

type NotificationWebhookDraft = Omit<NotificationWebhookWriteRequest, 'body_template'> & {
  body_template: string
  content_type: string
}

const DEFAULT_JSON_FIELDS: NotificationWebhookField[] = [
  { key: 'event.type', value: '{{ event.type }}' },
  { key: 'item.title', value: '{{ item.title }}' },
  { key: 'item.url', value: '{{ item.url }}' },
  { key: 'feed.name', value: '{{ feed.name }}' },
]

export function NotificationsPage() {
  const queryClient = useQueryClient()
  const [selectedWebhookId, setSelectedWebhookId] = useState<string | null>(null)
  const [draft, setDraft] = useState<NotificationWebhookDraft>(() => createDefaultDraft())
  const [sampleFeedId, setSampleFeedId] = useState('')
  const [formNotice, setFormNotice] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<NotificationWebhookTestResponse | null>(null)

  const webhooksQuery = useQuery({
    queryKey: ['notifications', 'webhooks'],
    queryFn: () => apiFetch<NotificationWebhook[]>('/notifications/webhooks'),
  })

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  const variablesQuery = useQuery({
    queryKey: ['notifications', 'template-variables'],
    queryFn: () => apiFetch<NotificationTemplateVariable[]>('/notifications/template-variables'),
  })

  const saveWebhook = useMutation({
    mutationFn: (payload: NotificationWebhookWriteRequest) => {
      if (selectedWebhookId) {
        return apiFetch<NotificationWebhook>(`/notifications/webhooks/${selectedWebhookId}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }

      return apiFetch<NotificationWebhook>('/notifications/webhooks', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: (saved) => {
      setSelectedWebhookId(saved.id)
      setDraft(createDraftFromWebhook(saved))
      setFormNotice(selectedWebhookId ? 'Webhook updated.' : 'Webhook created.')
      setTestResult(null)
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'webhooks'] })
    },
  })

  const deleteWebhook = useMutation({
    mutationFn: (webhookId: string) => apiFetch<void>(`/notifications/webhooks/${webhookId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setSelectedWebhookId(null)
      setDraft(createDefaultDraft())
      setSampleFeedId('')
      setFormNotice('Webhook deleted.')
      setTestResult(null)
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'webhooks'] })
    },
  })

  const testWebhook = useMutation({
    mutationFn: (payload: { webhook: NotificationWebhookWriteRequest; sample_feed_id?: string }) =>
      apiFetch<NotificationWebhookTestResponse>('/notifications/webhooks/test', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (result) => {
      setTestResult(result)
      setFormNotice(result.success ? 'Webhook test succeeded.' : 'Webhook test failed.')
    },
  })

  const feeds = feedsQuery.data ?? []
  const webhooks = webhooksQuery.data ?? []
  const variables = variablesQuery.data ?? []
  const testableFeeds = draft.feed_scope === 'selected' ? feeds.filter((feed) => draft.feed_ids.includes(feed.id)) : feeds

  useEffect(() => {
    if (!sampleFeedId) {
      return
    }

    if (!testableFeeds.some((feed) => feed.id == sampleFeedId)) {
      setSampleFeedId('')
    }
  }, [sampleFeedId, testableFeeds])

  const onSelectWebhook = (webhook: NotificationWebhook) => {
    setSelectedWebhookId(webhook.id)
    setDraft(createDraftFromWebhook(webhook))
    setSampleFeedId('')
    setFormNotice(null)
    setTestResult(null)
  }

  const onCreateNewWebhook = () => {
    setSelectedWebhookId(null)
    setDraft(createDefaultDraft())
    setSampleFeedId('')
    setFormNotice(null)
    setTestResult(null)
  }

  const onSave = () => {
    const normalizedDraft = normalizeDraftUrlQuery(draft)
    setDraft(normalizedDraft)
    setFormNotice(null)
    saveWebhook.mutate(createRequestFromDraft(normalizedDraft))
  }

  const onTest = () => {
    const normalizedDraft = normalizeDraftUrlQuery(draft)
    setDraft(normalizedDraft)
    setFormNotice(null)
    testWebhook.mutate({
      webhook: createRequestFromDraft(normalizedDraft),
      sample_feed_id: sampleFeedId || (normalizedDraft.feed_scope === 'selected' ? normalizedDraft.feed_ids[0] : undefined),
    })
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Webhook Notifications</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Configure outbound webhooks for new RSS items with templated URL parameters, headers, and payload fields.
        </p>
        <p className="mt-2 text-xs text-slate dark:text-white/60">
          Variables use `{'{{ item.title }}'}` style placeholders, similar to Grafana-style notification templates.
        </p>
      </section>

      <div className="grid gap-4 xl:grid-cols-[320px_1fr]">
        <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-display text-lg">Saved Webhooks</h3>
            <button
              className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40"
              onClick={onCreateNewWebhook}
            >
              New webhook
            </button>
          </div>

          <div className="mt-3 space-y-2">
            {webhooks.map((webhook) => {
              const selected = webhook.id === selectedWebhookId
              return (
                <button
                  key={webhook.id}
                  className={`w-full rounded-lg border p-3 text-left transition ${
                    selected
                      ? 'border-cyan bg-cyan/10 dark:border-cyan-500 dark:bg-cyan-950/50'
                      : 'border-slate/20 hover:border-slate/40 dark:border-cyan-900/40 dark:hover:border-cyan-700/60'
                  }`}
                  onClick={() => onSelectWebhook(webhook)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold">{webhook.name}</p>
                      <p className="mt-1 text-xs text-slate dark:text-white/65">
                        {webhook.method} · {describeFeedScope(webhook.feed_scope, webhook.feed_ids.length)}
                      </p>
                    </div>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                        webhook.enabled
                          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                          : 'bg-slate/10 text-slate-700 dark:bg-white/10 dark:text-white/65'
                      }`}
                    >
                      {webhook.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>
                  <p className="mt-2 truncate text-xs text-slate dark:text-white/65">{webhook.url_template}</p>
                </button>
              )
            })}

            {webhooksQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading webhooks...</p>}
            {webhooksQuery.isError && <p className="text-sm text-red-600">{resolveApiMessage(webhooksQuery.error, 'Failed to load webhooks.')}</p>}
            {!webhooksQuery.isLoading && !webhooks.length && (
              <p className="rounded-lg border border-dashed border-slate/25 p-3 text-sm text-slate dark:border-cyan-900/40 dark:text-white/70">
                No webhooks yet. Create one to call external systems when a new feed item lands.
              </p>
            )}
          </div>
        </section>

        <div className="space-y-4">
          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-lg">{selectedWebhookId ? 'Edit Webhook' : 'Create Webhook'}</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/75">Trigger: new RSS item ingested into ThreatLens.</p>
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

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <label className="text-sm font-semibold">Name</label>
                <input
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={draft.name}
                  onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                  placeholder="Slack ingest webhook"
                />
              </div>
              <div>
                <label className="text-sm font-semibold">HTTP Method</label>
                <select
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={draft.method}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      method: event.target.value as NotificationWebhookDraft['method'],
                    }))
                  }
                >
                  <option value="POST">POST</option>
                  <option value="PUT">PUT</option>
                  <option value="PATCH">PATCH</option>
                  <option value="GET">GET</option>
                  <option value="DELETE">DELETE</option>
                </select>
              </div>
              <div className="md:col-span-2">
                <label className="text-sm font-semibold">Webhook URL</label>
                <input
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={draft.url_template}
                  onChange={(event) => setDraft((current) => ({ ...current, url_template: event.target.value }))}
                  onBlur={() => setDraft((current) => normalizeDraftUrlQuery(current))}
                  placeholder="https://hooks.example.com/notify?source={{ feed.name }}"
                />
                <p className="mt-1 text-xs text-slate dark:text-white/60">
                  Query strings like `?token=abc` are automatically moved into Query Parameters.
                </p>
              </div>
              <div>
                <label className="text-sm font-semibold">Timeout (seconds)</label>
                <input
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  type="number"
                  min={1}
                  max={60}
                  value={draft.timeout_seconds}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      timeout_seconds: Number(event.target.value) || 10,
                    }))
                  }
                />
              </div>
              <div>
                <label className="text-sm font-semibold">Body Mode</label>
                <select
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={draft.body_mode}
                  onChange={(event) => setDraft((current) => applyBodyMode(current, event.target.value as NotificationWebhookDraft['body_mode']))}
                >
                  <option value="json">JSON object</option>
                  <option value="form">Form fields</option>
                  <option value="raw">Raw body</option>
                  <option value="none">No body</option>
                </select>
              </div>
              <div>
                <label className="text-sm font-semibold">Content Type</label>
                <input
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  list="notification-content-types"
                  value={draft.content_type}
                  onChange={(event) => setDraft((current) => ({ ...current, content_type: event.target.value }))}
                  placeholder={`Auto (${resolveDefaultContentTypeLabel(draft.body_mode)})`}
                />
                <datalist id="notification-content-types">
                  <option value="application/json" />
                  <option value="application/x-www-form-urlencoded" />
                  <option value="text/plain; charset=utf-8" />
                  <option value="text/markdown; charset=utf-8" />
                </datalist>
                <p className="mt-1 text-xs text-slate dark:text-white/60">
                  Leave blank to use the default for the selected body mode.
                </p>
              </div>
            </div>

            <div className="mt-5 rounded-lg border border-slate/20 p-4 dark:border-cyan-900/40">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h4 className="font-semibold">Feed Scope</h4>
                  <p className="mt-1 text-xs text-slate dark:text-white/65">Choose whether this webhook fires for any feed or only selected feeds.</p>
                </div>
                <div className="flex rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40">
                  <button
                    className={`rounded px-3 py-1 text-sm ${
                      draft.feed_scope === 'all' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'
                    }`}
                    onClick={() => setDraft((current) => ({ ...current, feed_scope: 'all', feed_ids: [] }))}
                  >
                    Any feed
                  </button>
                  <button
                    className={`rounded px-3 py-1 text-sm ${
                      draft.feed_scope === 'selected'
                        ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                        : 'text-slate dark:text-white/75'
                    }`}
                    onClick={() => setDraft((current) => ({ ...current, feed_scope: 'selected' }))}
                  >
                    Selected feeds
                  </button>
                </div>
              </div>

              {draft.feed_scope === 'selected' && (
                <div className="mt-4 grid gap-2 md:grid-cols-2">
                  {feeds.map((feed) => {
                    const checked = draft.feed_ids.includes(feed.id)
                    return (
                      <label key={feed.id} className="flex items-start gap-3 rounded border border-slate/20 p-3 text-sm dark:border-cyan-900/40">
                        <input
                          className="mt-1"
                          type="checkbox"
                          checked={checked}
                          onChange={() => setDraft((current) => toggleFeedSelection(current, feed.id))}
                        />
                        <span>
                          <span className="block font-semibold">{feed.name}</span>
                          <span className="text-xs text-slate dark:text-white/60">{feed.url}</span>
                        </span>
                      </label>
                    )
                  })}
                  {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading feeds...</p>}
                  {feedsQuery.isError && <p className="text-sm text-red-600">{resolveApiMessage(feedsQuery.error, 'Failed to load feeds.')}</p>}
                </div>
              )}
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-2">
              <KeyValueEditor
                title="Query Parameters"
                description="Append rendered arguments to the webhook URL."
                fields={draft.query_params}
                addLabel="Add parameter"
                keyPlaceholder="title"
                valuePlaceholder="{{ item.title }}"
                onChange={(fields) => setDraft((current) => ({ ...current, query_params: fields }))}
              />
              <KeyValueEditor
                title="Headers"
                description="Custom headers such as authorization, routing, or tenant IDs. Content-Type is managed separately above."
                fields={draft.headers}
                addLabel="Add header"
                keyPlaceholder="Authorization"
                valuePlaceholder="Bearer {{ user.email }}"
                onChange={(fields) => setDraft((current) => ({ ...current, headers: fields }))}
              />
            </div>

            {draft.body_mode === 'json' || draft.body_mode === 'form' ? (
              <div className="mt-4">
                <KeyValueEditor
                  title={draft.body_mode === 'json' ? 'JSON Body Fields' : 'Form Fields'}
                  description={
                    draft.body_mode === 'json'
                      ? 'Use dotted keys like `item.title` to build nested JSON safely.'
                      : 'Rendered form fields are sent as `application/x-www-form-urlencoded`.'
                  }
                  fields={draft.body_fields}
                  addLabel={draft.body_mode === 'json' ? 'Add JSON field' : 'Add form field'}
                  keyPlaceholder={draft.body_mode === 'json' ? 'item.title' : 'title'}
                  valuePlaceholder="{{ item.title }}"
                  onChange={(fields) => setDraft((current) => ({ ...current, body_fields: fields }))}
                />
              </div>
            ) : null}

            {draft.body_mode === 'raw' && (
              <div className="mt-4">
                <label className="text-sm font-semibold">Raw Body Template</label>
                <textarea
                  className="mt-1 h-40 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={draft.body_template}
                  onChange={(event) => setDraft((current) => ({ ...current, body_template: event.target.value }))}
                  placeholder={`{"title":"{{ item.title }}","url":"{{ item.url }}"}`}
                />
              </div>
            )}

            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button
                className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]"
                disabled={saveWebhook.isPending}
                onClick={onSave}
              >
                {selectedWebhookId ? 'Save changes' : 'Create webhook'}
              </button>
              <button
                className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
                disabled={testWebhook.isPending || (draft.feed_scope === 'selected' && !draft.feed_ids.length)}
                onClick={onTest}
              >
                Test webhook
              </button>
              {selectedWebhookId && (
                <button
                  className="rounded border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 dark:border-red-900/60 dark:text-red-300"
                  disabled={deleteWebhook.isPending}
                  onClick={() => deleteWebhook.mutate(selectedWebhookId)}
                >
                  Delete webhook
                </button>
              )}
              {(draft.feed_scope === 'all' || draft.feed_ids.length > 1) && (
                <select
                  className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={sampleFeedId}
                  onChange={(event) => setSampleFeedId(event.target.value)}
                >
                  <option value="">Auto sample feed</option>
                  {testableFeeds.map((feed) => (
                    <option key={feed.id} value={feed.id}>
                      {feed.name}
                    </option>
                  ))}
                </select>
              )}
            </div>

            {formNotice && (
              <p className={`mt-3 text-sm ${testResult && !testResult.success ? 'text-red-600' : 'text-emerald-700 dark:text-emerald-300'}`}>
                {formNotice}
              </p>
            )}
            {saveWebhook.isError && <p className="mt-2 text-sm text-red-600">{resolveApiMessage(saveWebhook.error, 'Failed to save webhook.')}</p>}
            {deleteWebhook.isError && (
              <p className="mt-2 text-sm text-red-600">{resolveApiMessage(deleteWebhook.error, 'Failed to delete webhook.')}</p>
            )}
            {testWebhook.isError && <p className="mt-2 text-sm text-red-600">{resolveApiMessage(testWebhook.error, 'Failed to test webhook.')}</p>}
          </section>

          <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
            <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
              <div className="flex items-center justify-between gap-3">
                <h3 className="font-display text-lg">Test Result</h3>
                {testResult && (
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      testResult.success
                        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                        : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                    }`}
                  >
                    {testResult.success ? 'Success' : 'Failed'}
                  </span>
                )}
              </div>

              {testResult ? (
                <div className="mt-3 space-y-3 text-sm">
                  <div className="grid gap-3 md:grid-cols-3">
                    <MetricCard label="Method" value={testResult.rendered_method} />
                    <MetricCard label="Status" value={testResult.status_code ? String(testResult.status_code) : 'n/a'} />
                    <MetricCard label="Duration" value={testResult.duration_ms != null ? `${testResult.duration_ms} ms` : 'n/a'} />
                  </div>

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate dark:text-white/60">Rendered URL</p>
                    <code className="mt-1 block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{testResult.rendered_url}</code>
                  </div>

                  {testResult.rendered_headers.length > 0 && (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate dark:text-white/60">Headers</p>
                      <div className="mt-1 space-y-1">
                        {testResult.rendered_headers.map((header, index) => (
                          <code key={`${header.key}-${index}`} className="block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">
                            {header.key}: {header.value}
                          </code>
                        ))}
                      </div>
                    </div>
                  )}

                  {testResult.rendered_body && (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate dark:text-white/60">Rendered Body</p>
                      <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{testResult.rendered_body}</pre>
                    </div>
                  )}

                  {testResult.response_body_preview && (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate dark:text-white/60">Response Preview</p>
                      <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">
                        {testResult.response_body_preview}
                      </pre>
                    </div>
                  )}

                  {testResult.error && <p className="text-sm text-red-600">{testResult.error}</p>}
                </div>
              ) : (
                <p className="mt-3 text-sm text-slate dark:text-white/70">
                  Run a test delivery to inspect the fully rendered request and the webhook response.
                </p>
              )}
            </div>

            <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
              <h3 className="font-display text-lg">Available Variables</h3>
              <p className="mt-1 text-sm text-slate dark:text-white/75">Use these placeholders anywhere in the URL, headers, query parameters, or body.</p>

              <div className="mt-3 space-y-2">
                {variables.map((variable) => (
                  <div key={variable.key} className="rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
                    <code className="text-xs font-semibold">{`{{ ${variable.key} }}`}</code>
                    <p className="mt-1 text-sm">{variable.description}</p>
                    <p className="mt-1 text-xs text-slate dark:text-white/60">Example: {variable.example}</p>
                  </div>
                ))}

                {variablesQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading variables...</p>}
                {variablesQuery.isError && (
                  <p className="text-sm text-red-600">{resolveApiMessage(variablesQuery.error, 'Failed to load template variables.')}</p>
                )}
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}

function KeyValueEditor({
  title,
  description,
  fields,
  addLabel,
  keyPlaceholder,
  valuePlaceholder,
  onChange,
}: {
  title: string
  description: string
  fields: NotificationWebhookField[]
  addLabel: string
  keyPlaceholder: string
  valuePlaceholder: string
  onChange: (fields: NotificationWebhookField[]) => void
}) {
  return (
    <section className="rounded-lg border border-slate/20 p-4 dark:border-cyan-900/40">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="font-semibold">{title}</h4>
          <p className="mt-1 text-xs text-slate dark:text-white/65">{description}</p>
        </div>
        <button className="rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold dark:border-cyan-900/40" onClick={() => onChange([...fields, emptyField()])}>
          {addLabel}
        </button>
      </div>

      <div className="mt-3 space-y-2">
        {fields.length === 0 && <p className="text-sm text-slate dark:text-white/70">No entries yet.</p>}
        {fields.map((field, index) => (
          <div key={`${title}-${index}`} className="grid gap-2 md:grid-cols-[1fr_1.4fr_auto]">
            <input
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={field.key}
              onChange={(event) => onChange(updateField(fields, index, { key: event.target.value }))}
              placeholder={keyPlaceholder}
            />
            <input
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={field.value}
              onChange={(event) => onChange(updateField(fields, index, { value: event.target.value }))}
              placeholder={valuePlaceholder}
            />
            <button
              className="rounded border border-slate/30 px-3 py-2 text-sm dark:border-cyan-900/40"
              onClick={() => onChange(fields.filter((_, candidateIndex) => candidateIndex !== index))}
            >
              Remove
            </button>
          </div>
        ))}
      </div>
    </section>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate dark:text-white/60">{label}</p>
      <p className="mt-1 text-sm font-semibold">{value}</p>
    </div>
  )
}

function createDefaultDraft(): NotificationWebhookDraft {
  return {
    name: '',
    enabled: true,
    event_type: 'rss_item_new',
    url_template: '',
    method: 'POST',
    feed_scope: 'all',
    feed_ids: [],
    query_params: [],
    headers: [],
    body_mode: 'json',
    body_fields: DEFAULT_JSON_FIELDS.map((field) => ({ ...field })),
    body_template: '',
    content_type: '',
    timeout_seconds: 10,
  }
}

function createDraftFromWebhook(webhook: NotificationWebhook): NotificationWebhookDraft {
  const { contentType, headers } = splitContentTypeHeader(webhook.headers)
  return {
    name: webhook.name,
    enabled: webhook.enabled,
    event_type: webhook.event_type,
    url_template: webhook.url_template,
    method: webhook.method,
    feed_scope: webhook.feed_scope,
    feed_ids: [...webhook.feed_ids],
    query_params: webhook.query_params.map((field) => ({ ...field })),
    headers: headers.map((field) => ({ ...field })),
    body_mode: webhook.body_mode,
    body_fields: webhook.body_fields.map((field) => ({ ...field })),
    body_template: webhook.body_template ?? '',
    content_type: contentType,
    timeout_seconds: webhook.timeout_seconds,
  }
}

function createRequestFromDraft(draft: NotificationWebhookDraft): NotificationWebhookWriteRequest {
  const normalizedDraft = normalizeDraftUrlQuery(draft)
  return {
    name: normalizedDraft.name.trim(),
    enabled: normalizedDraft.enabled,
    event_type: 'rss_item_new',
    url_template: normalizedDraft.url_template.trim(),
    method: normalizedDraft.method,
    feed_scope: normalizedDraft.feed_scope,
    feed_ids: normalizedDraft.feed_scope === 'selected' ? normalizedDraft.feed_ids : [],
    query_params: sanitizeFields(normalizedDraft.query_params),
    headers: buildRequestHeaders(normalizedDraft),
    body_mode: normalizedDraft.body_mode,
    body_fields: normalizedDraft.body_mode === 'json' || normalizedDraft.body_mode === 'form' ? sanitizeFields(normalizedDraft.body_fields) : [],
    body_template: normalizedDraft.body_mode === 'raw' ? normalizedDraft.body_template : null,
    timeout_seconds: normalizedDraft.timeout_seconds,
  }
}

function sanitizeFields(fields: NotificationWebhookField[]): NotificationWebhookField[] {
  return fields
    .map((field) => ({ key: field.key.trim(), value: field.value }))
    .filter((field) => field.key.length > 0)
}

function applyBodyMode(current: NotificationWebhookDraft, nextBodyMode: NotificationWebhookDraft['body_mode']): NotificationWebhookDraft {
  if (nextBodyMode === 'json') {
    return {
      ...current,
      body_mode: 'json',
      body_fields: current.body_fields.length ? current.body_fields : DEFAULT_JSON_FIELDS.map((field) => ({ ...field })),
      body_template: '',
    }
  }

  if (nextBodyMode === 'form') {
    return {
      ...current,
      body_mode: 'form',
      body_fields: current.body_fields.length ? current.body_fields : [emptyField()],
      body_template: '',
    }
  }

  if (nextBodyMode === 'raw') {
    return {
      ...current,
      body_mode: 'raw',
      body_template: current.body_template || '{"title":"{{ item.title }}","url":"{{ item.url }}"}',
    }
  }

  return {
    ...current,
    body_mode: 'none',
    body_fields: [],
    body_template: '',
  }
}

function normalizeDraftUrlQuery(current: NotificationWebhookDraft): NotificationWebhookDraft {
  const extracted = extractUrlQueryParams(current.url_template)
  if (extracted == null) {
    return current
  }

  return {
    ...current,
    url_template: extracted.baseUrl,
    query_params: mergeQueryParams(current.query_params, extracted.queryParams),
  }
}

function toggleFeedSelection(current: NotificationWebhookDraft, feedId: string): NotificationWebhookDraft {
  const alreadySelected = current.feed_ids.includes(feedId)
  return {
    ...current,
    feed_scope: 'selected',
    feed_ids: alreadySelected ? current.feed_ids.filter((candidate) => candidate !== feedId) : [...current.feed_ids, feedId],
  }
}

function updateField(fields: NotificationWebhookField[], index: number, patch: Partial<NotificationWebhookField>): NotificationWebhookField[] {
  return fields.map((field, candidateIndex) => (candidateIndex === index ? { ...field, ...patch } : field))
}

function emptyField(): NotificationWebhookField {
  return { key: '', value: '' }
}

function splitContentTypeHeader(headers: NotificationWebhookField[]): { contentType: string; headers: NotificationWebhookField[] } {
  let contentType = ''
  const remainingHeaders: NotificationWebhookField[] = []

  for (const header of headers) {
    if (header.key.trim().toLowerCase() === 'content-type') {
      if (!contentType) {
        contentType = header.value
      }
      continue
    }
    remainingHeaders.push({ ...header })
  }

  return { contentType, headers: remainingHeaders }
}

function buildRequestHeaders(draft: NotificationWebhookDraft): NotificationWebhookField[] {
  const headers = sanitizeFields(draft.headers).filter((header) => header.key.trim().toLowerCase() !== 'content-type')
  const contentType = draft.content_type.trim()
  if (contentType) {
    headers.push({ key: 'Content-Type', value: contentType })
  }
  return headers
}

function extractUrlQueryParams(urlTemplate: string): { baseUrl: string; queryParams: NotificationWebhookField[] } | null {
  const trimmedUrl = urlTemplate.trim()
  const queryIndex = trimmedUrl.indexOf('?')
  if (queryIndex === -1) {
    return null
  }

  const hashIndex = trimmedUrl.indexOf('#', queryIndex)
  const baseUrl = hashIndex === -1 ? trimmedUrl.slice(0, queryIndex) : `${trimmedUrl.slice(0, queryIndex)}${trimmedUrl.slice(hashIndex)}`
  const rawQuery = hashIndex === -1 ? trimmedUrl.slice(queryIndex + 1) : trimmedUrl.slice(queryIndex + 1, hashIndex)
  const queryParams = rawQuery
    .split('&')
    .map((segment) => segment.trim())
    .filter(Boolean)
    .map((segment) => {
      const separatorIndex = segment.indexOf('=')
      const rawKey = separatorIndex === -1 ? segment : segment.slice(0, separatorIndex)
      const rawValue = separatorIndex === -1 ? '' : segment.slice(separatorIndex + 1)
      return {
        key: decodeQueryComponent(rawKey),
        value: decodeQueryComponent(rawValue),
      }
    })
    .filter((field) => field.key.trim().length > 0)

  if (!queryParams.length) {
    return { baseUrl, queryParams: [] }
  }

  return { baseUrl, queryParams }
}

function mergeQueryParams(existing: NotificationWebhookField[], extracted: NotificationWebhookField[]): NotificationWebhookField[] {
  if (!extracted.length) {
    return existing
  }

  const extractedKeys = new Set(extracted.map((field) => field.key))
  return [...existing.filter((field) => !extractedKeys.has(field.key)), ...extracted]
}

function decodeQueryComponent(value: string): string {
  const normalized = value.replace(/\+/g, ' ')
  try {
    return decodeURIComponent(normalized)
  } catch {
    return normalized
  }
}

function resolveDefaultContentTypeLabel(bodyMode: NotificationWebhookDraft['body_mode']): string {
  if (bodyMode === 'json') {
    return 'application/json'
  }
  if (bodyMode === 'form') {
    return 'application/x-www-form-urlencoded'
  }
  if (bodyMode === 'raw') {
    return 'auto detect'
  }
  return 'none'
}

function describeFeedScope(scope: NotificationWebhook['feed_scope'], count: number): string {
  if (scope === 'all') {
    return 'all feeds'
  }
  return `${count} selected feed${count === 1 ? '' : 's'}`
}

function resolveApiMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return error.message
  }
  return fallback
}
