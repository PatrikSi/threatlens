import { resolveApiErrorMessage } from '../api/errors'
import { NotificationEventType } from '../types/api'
import {
  applyBodyMode,
  applyEventType,
  describeEventDescription,
  normalizeDraftUrlQuery,
  NotificationWebhookDraft,
  resolveDefaultContentTypeLabel,
  toggleFeedSelection,
} from './notificationWebhookDraft'
import { KeyValueEditor } from './NotificationWebhookShared'
import { NotificationWebhooksController } from './useNotificationWebhooksController'

function UnavailableAIEventNotice({ visible, feature }: { visible: boolean; feature: string }) {
  if (!visible) return null
  return (
    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
      This existing selection is inactive until {feature} is enabled and configured.
    </p>
  )
}

function BasicRequestFields({ controller }: { controller: NotificationWebhooksController }) {
  const { availableEventOptions, canManageWebhooks, draft, setDraft, unavailableDailyBriefSelected, unavailableReportSelected } = controller
  return (
    <div className="mt-3 grid gap-3 md:grid-cols-2">
      <div>
        <label htmlFor="notification-webhook-name" className="text-sm font-semibold">Name</label>
        <input
          id="notification-webhook-name"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
          disabled={!canManageWebhooks}
          value={draft.name}
          onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
          placeholder="Slack ingest webhook"
        />
      </div>
      <div>
        <label htmlFor="notification-webhook-event-type" className="text-sm font-semibold">Event type</label>
        <select
          id="notification-webhook-event-type"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
          disabled={!canManageWebhooks}
          value={draft.event_type}
          onChange={(event) => setDraft((current) => applyEventType(current, event.target.value as NotificationEventType))}
        >
          {availableEventOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <p className="mt-1 text-xs text-slate dark:text-white/60">{describeEventDescription(draft.event_type)}</p>
        <UnavailableAIEventNotice visible={unavailableDailyBriefSelected} feature="AI daily brief generation" />
        <UnavailableAIEventNotice visible={unavailableReportSelected} feature="AI reporting" />
      </div>
      <div>
        <label htmlFor="notification-webhook-method" className="text-sm font-semibold">HTTP method</label>
        <select
          id="notification-webhook-method"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
          disabled={!canManageWebhooks}
          value={draft.method}
          onChange={(event) => setDraft((current) => ({ ...current, method: event.target.value as NotificationWebhookDraft['method'] }))}
        >
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="PATCH">PATCH</option>
          <option value="GET">GET</option>
          <option value="DELETE">DELETE</option>
        </select>
      </div>
      <div className="md:col-span-2">
        <label htmlFor="notification-webhook-url" className="text-sm font-semibold">Webhook URL</label>
        <input
          id="notification-webhook-url"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
          disabled={!canManageWebhooks}
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
        <label htmlFor="notification-webhook-timeout" className="text-sm font-semibold">Timeout (seconds)</label>
        <input
          id="notification-webhook-timeout"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
          type="number"
          min={1}
          max={60}
          disabled={!canManageWebhooks}
          value={draft.timeout_seconds}
          onChange={(event) => setDraft((current) => ({ ...current, timeout_seconds: Number(event.target.value) || 10 }))}
        />
      </div>
      <div>
        <label htmlFor="notification-webhook-body-mode" className="text-sm font-semibold">Body mode</label>
        <select
          id="notification-webhook-body-mode"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
          disabled={!canManageWebhooks}
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
        <label htmlFor="notification-webhook-content-type" className="text-sm font-semibold">Content type</label>
        <input
          id="notification-webhook-content-type"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
          list="notification-content-types"
          disabled={!canManageWebhooks}
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
        <p className="mt-1 text-xs text-slate dark:text-white/60">Leave blank to use the default for the selected body mode.</p>
      </div>
    </div>
  )
}

function FeedScopeEditor({ controller }: { controller: NotificationWebhooksController }) {
  const { canManageWebhooks, draft, feeds, feedsQuery, setDraft } = controller
  return (
    <div className="mt-4 rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold">Feed scope</h3>
          <p className="mt-1 text-xs text-slate dark:text-white/65">Choose whether this webhook fires for any feed or only selected feeds.</p>
        </div>
        <div
          role="group"
          aria-label="Webhook feed scope"
          className={`flex rounded-lg border p-1 ${canManageWebhooks ? 'border-slate/20 dark:border-cyan-900/40' : 'border-slate/15 dark:border-white/10'}`}
        >
          <button
            type="button"
            aria-pressed={draft.feed_scope === 'all'}
            disabled={!canManageWebhooks}
            className={`rounded px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${draft.feed_scope === 'all' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'}`}
            onClick={() => setDraft((current) => ({ ...current, feed_scope: 'all', feed_ids: [] }))}
          >
            Any feed
          </button>
          <button
            type="button"
            aria-pressed={draft.feed_scope === 'selected'}
            disabled={!canManageWebhooks}
            className={`rounded px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${draft.feed_scope === 'selected' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'}`}
            onClick={() => setDraft((current) => ({ ...current, feed_scope: 'selected' }))}
          >
            Selected feeds
          </button>
        </div>
      </div>
      {draft.feed_scope === 'selected' && (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {feeds.map((feed) => (
            <label
              key={feed.id}
              className={`flex items-start gap-3 rounded border p-3 text-sm ${canManageWebhooks ? 'border-slate/20 dark:border-cyan-900/40' : 'border-slate/15 dark:border-white/10'}`}
            >
              <input
                className="mt-1"
                type="checkbox"
                checked={draft.feed_ids.includes(feed.id)}
                disabled={!canManageWebhooks}
                onChange={() => setDraft((current) => toggleFeedSelection(current, feed.id))}
              />
              <span>
                <span className="block font-semibold">{feed.name}</span>
                <span className="text-xs text-slate dark:text-white/60">{feed.url}</span>
              </span>
            </label>
          ))}
          {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading feeds...</p>}
          {feedsQuery.isError && <p className="text-sm text-red-600">{resolveApiErrorMessage(feedsQuery.error, 'Failed to load feeds.')}</p>}
        </div>
      )}
    </div>
  )
}

function RequestPayloadEditors({ controller }: { controller: NotificationWebhooksController }) {
  const { canManageWebhooks, draft, setDraft } = controller
  return (
    <>
      <div className="mt-4 grid gap-3 xl:grid-cols-2">
        <KeyValueEditor
          title="Query parameters"
          description="Append rendered arguments to the webhook URL."
          fields={draft.query_params}
          addLabel="Add parameter"
          keyPlaceholder="title"
          valuePlaceholder="{{ item.title }}"
          disabled={!canManageWebhooks}
          onChange={(fields) => setDraft((current) => ({ ...current, query_params: fields }))}
        />
        <KeyValueEditor
          title="Headers"
          description="Custom headers such as authorization, routing, or tenant IDs. Content-Type is managed separately above."
          fields={draft.headers}
          addLabel="Add header"
          keyPlaceholder="Authorization"
          valuePlaceholder="Bearer {{ user.email }}"
          disabled={!canManageWebhooks}
          onChange={(fields) => setDraft((current) => ({ ...current, headers: fields }))}
        />
      </div>
      {(draft.body_mode === 'json' || draft.body_mode === 'form') && (
        <div className="mt-3">
          <KeyValueEditor
            title={draft.body_mode === 'json' ? 'JSON body fields' : 'Form fields'}
            description={draft.body_mode === 'json' ? 'Use dotted keys like `item.title` to build nested JSON safely.' : 'Rendered form fields are sent as `application/x-www-form-urlencoded`.'}
            fields={draft.body_fields}
            addLabel={draft.body_mode === 'json' ? 'Add JSON field' : 'Add form field'}
            keyPlaceholder={draft.body_mode === 'json' ? 'item.title' : 'title'}
            valuePlaceholder="{{ item.title }}"
            disabled={!canManageWebhooks}
            onChange={(fields) => setDraft((current) => ({ ...current, body_fields: fields }))}
          />
        </div>
      )}
      {draft.body_mode === 'raw' && (
        <div className="mt-3">
          <label htmlFor="notification-webhook-raw-body" className="text-sm font-semibold">Raw body template</label>
          <textarea
            id="notification-webhook-raw-body"
            className="mt-1 h-32 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
            disabled={!canManageWebhooks}
            value={draft.body_template}
            onChange={(event) => setDraft((current) => ({ ...current, body_template: event.target.value }))}
            placeholder={'{"title":"{{ item.title }}","url":"{{ item.url }}"}'}
          />
        </div>
      )}
    </>
  )
}

function EditorActions({ controller }: { controller: NotificationWebhooksController }) {
  const {
    canManageWebhooks,
    deleteWebhook,
    draft,
    isReadOnlyViewer,
    onRequestDeleteWebhook,
    onSave,
    onTest,
    pendingWebhookDelete,
    sampleFeedId,
    saveWebhook,
    selectedWebhookId,
    setSampleFeedId,
    testableFeeds,
    testWebhook,
    webhooks,
  } = controller
  if (!canManageWebhooks || isReadOnlyViewer) return null
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2">
      <button className="rounded bg-ink px-3 py-2 text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]" disabled={saveWebhook.isPending} onClick={onSave}>
        {selectedWebhookId ? 'Save changes' : 'Create webhook'}
      </button>
      <button
        className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
        disabled={testWebhook.isPending || (draft.feed_scope === 'selected' && !draft.feed_ids.length)}
        onClick={onTest}
      >
        Test webhook
      </button>
      {selectedWebhookId && (
        <button
          className="rounded border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50 dark:border-red-900/60 dark:text-red-300"
          disabled={deleteWebhook.isPending || Boolean(pendingWebhookDelete)}
          onClick={() => onRequestDeleteWebhook(webhooks.find((entry) => entry.id === selectedWebhookId) ?? null)}
        >
          Delete webhook
        </button>
      )}
      {(draft.feed_scope === 'all' || draft.feed_ids.length > 1) && (
        <>
          <label htmlFor="notification-sample-feed" className="sr-only">Sample feed</label>
          <select
            id="notification-sample-feed"
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
            value={sampleFeedId}
            onChange={(event) => setSampleFeedId(event.target.value)}
          >
            <option value="">Auto sample feed</option>
            {testableFeeds.map((feed) => <option key={feed.id} value={feed.id}>{feed.name}</option>)}
          </select>
        </>
      )}
    </div>
  )
}

function EditorNotices({ controller }: { controller: NotificationWebhooksController }) {
  const { deleteWebhook, formNotice, saveWebhook, testResult, testWebhook } = controller
  return (
    <>
      {formNotice && (
        <p
          role={testResult && !testResult.success ? 'alert' : 'status'}
          aria-live={testResult && !testResult.success ? 'assertive' : 'polite'}
          aria-atomic="true"
          className={`mt-3 text-sm ${testResult && !testResult.success ? 'text-red-600' : 'text-emerald-700 dark:text-emerald-300'}`}
        >
          {formNotice}
        </p>
      )}
      {saveWebhook.isError && <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">{resolveApiErrorMessage(saveWebhook.error, 'Failed to save webhook.')}</p>}
      {deleteWebhook.isError && <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">{resolveApiErrorMessage(deleteWebhook.error, 'Failed to delete webhook.')}</p>}
      {testWebhook.isError && <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">{resolveApiErrorMessage(testWebhook.error, 'Failed to test webhook.')}</p>}
    </>
  )
}

export function NotificationWebhookEditor({ controller }: { controller: NotificationWebhooksController }) {
  const { canManageWebhooks, draft, selectedWebhookId, setDraft } = controller
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-lg">{selectedWebhookId ? 'Edit webhook' : 'Create webhook'}</h2>
        <label className={`flex items-center gap-2 rounded-full border px-3 py-1 text-sm ${canManageWebhooks ? 'border-slate/20 dark:border-cyan-900/40' : 'border-slate/15 text-slate/70 dark:border-white/10 dark:text-white/55'}`}>
          <input type="checkbox" checked={draft.enabled} disabled={!canManageWebhooks} onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))} />
          Enabled
        </label>
      </div>
      <BasicRequestFields controller={controller} />
      <FeedScopeEditor controller={controller} />
      <RequestPayloadEditors controller={controller} />
      <EditorActions controller={controller} />
      <EditorNotices controller={controller} />
    </section>
  )
}

export function WebhookEditorUnavailable({ controller }: { controller: NotificationWebhooksController }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Webhook editor</p>
      <h2 className="mt-1 font-display text-lg">Changes unavailable</h2>
      <p className="mt-2 text-sm text-slate dark:text-white/75">{controller.webhookEditorBlockedNotice}</p>
      {controller.webhooks.length > 0 && (
        <p className="mt-3 text-sm text-slate dark:text-white/70">
          Select a saved webhook to inspect its current configuration in read-only mode.
        </p>
      )}
    </section>
  )
}
