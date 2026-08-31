import { resolveApiErrorMessage } from '../api/errors'
import { describeEventType } from './notificationWebhookDraft'
import { describeFeedScope } from './notificationWebhookPresentation'
import { MetricCard } from './NotificationWebhookShared'
import { NotificationWebhooksController } from './useNotificationWebhooksController'

export function SavedWebhooksCard({ controller }: { controller: NotificationWebhooksController }) {
  const { canManageWebhooks, onCreateNewWebhook, onSelectWebhook, selectedWebhookId, webhooks, webhooksQuery } = controller
  return (
    <section className="min-w-0 rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-display text-lg">Configured webhooks</h2>
        {canManageWebhooks && (
          <button
            className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40"
            onClick={onCreateNewWebhook}
          >
            New webhook
          </button>
        )}
      </div>
      <div className="mt-3 max-h-[28rem] overflow-auto rounded-lg border border-slate/20 dark:border-cyan-900/40">
        {webhooks.map((webhook) => {
          const selected = webhook.id === selectedWebhookId
          return (
            <button
              key={webhook.id}
              type="button"
              aria-pressed={selected}
              className={`grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-slate/10 px-3 py-2 text-left text-sm transition last:border-b-0 dark:border-cyan-900/30 ${
                selected ? 'bg-cyan/10 dark:bg-cyan-950/50' : 'hover:bg-slate/5 dark:hover:bg-white/[0.03]'
              }`}
              onClick={() => onSelectWebhook(webhook)}
            >
              <div className="min-w-0">
                <p className="truncate font-semibold">{webhook.name}</p>
                <p className="mt-0.5 truncate text-xs text-slate dark:text-white/65">
                  {describeEventType(webhook.event_type)} · {webhook.method} ·{' '}
                  {describeFeedScope(webhook.feed_scope, webhook.feed_ids.length)}
                </p>
                <p className="mt-0.5 truncate text-xs text-slate dark:text-white/55">{webhook.url_template}</p>
              </div>
              <span className={`tl-chip ${webhook.enabled ? 'tl-chip-success' : 'tl-chip-neutral'}`}>
                {webhook.enabled ? 'Enabled' : 'Disabled'}
              </span>
            </button>
          )
        })}
      </div>
      {webhooksQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-white/70">Loading webhooks...</p>}
      {webhooksQuery.isError && (
        <p className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(webhooksQuery.error, 'Failed to load webhooks.')}
        </p>
      )}
      {!webhooksQuery.isLoading && !webhooks.length && (
        <p className="mt-3 rounded-lg border border-dashed border-slate/25 p-3 text-sm text-slate dark:border-cyan-900/40 dark:text-white/70">
          No webhooks yet. Create one to call external systems when ThreatLens sees new items, alert matches, feed failures, or digest windows.
        </p>
      )}
    </section>
  )
}

export function TestResultAndVariables({ controller }: { controller: NotificationWebhooksController }) {
  const {
    mobileVariablesOpen,
    setMobileVariablesOpen,
    testResult,
    variables,
    variablesQuery,
  } = controller
  return (
    <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-display text-lg">Test result</h2>
          {testResult && (
            <span className={`tl-chip ${testResult.success ? 'tl-chip-success' : 'tl-chip-danger'}`}>
              {testResult.success ? 'Success' : 'Failed'}
            </span>
          )}
        </div>
        {testResult ? (
          <div className="mt-3 space-y-3 text-sm">
            <p className="text-xs text-slate dark:text-white/60">
              Sensitive URL parameters, headers, request bodies, and response previews are redacted before display.
            </p>
            <div className="grid gap-3 md:grid-cols-3">
              <MetricCard label="Method" value={testResult.rendered_method} />
              <MetricCard label="Status" value={testResult.status_code ? String(testResult.status_code) : 'n/a'} />
              <MetricCard label="Duration" value={testResult.duration_ms != null ? `${testResult.duration_ms} ms` : 'n/a'} />
            </div>
            <div>
              <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Rendered URL</p>
              <code className="mt-1 block rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{testResult.rendered_url}</code>
            </div>
            {testResult.rendered_headers.length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Headers</p>
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
                <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Rendered body</p>
                <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{testResult.rendered_body}</pre>
              </div>
            )}
            {testResult.response_body_preview && (
              <div>
                <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Response preview</p>
                <pre className="mt-1 overflow-x-auto rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">
                  {testResult.response_body_preview}
                </pre>
              </div>
            )}
            {testResult.error && <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">{testResult.error}</p>}
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate dark:text-white/70">
            Run a test delivery to inspect a redacted request preview and the webhook response summary.
          </p>
        )}
      </div>

      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-lg">Available variables</h2>
            <p className="mt-1 text-sm text-slate dark:text-white/75">
              Use these placeholders anywhere in the URL, headers, query parameters, or body.
            </p>
          </div>
          <button
            type="button"
            className="shrink-0 rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold sm:hidden dark:border-cyan-900/40"
            aria-expanded={mobileVariablesOpen}
            aria-controls="webhook-template-variables"
            onClick={() => setMobileVariablesOpen((current) => !current)}
          >
            {mobileVariablesOpen ? 'Hide' : `Show ${variables.length}`}
          </button>
        </div>
        <div
          id="webhook-template-variables"
          className={`${mobileVariablesOpen ? 'block' : 'hidden'} mt-3 space-y-0 overflow-hidden rounded border border-slate/20 sm:block sm:space-y-2 sm:overflow-visible sm:rounded-none sm:border-0 dark:border-cyan-900/40`}
        >
          {variables.map((variable) => (
            <div key={variable.key} className="border-b border-slate/15 p-2.5 last:border-b-0 sm:rounded-lg sm:border sm:border-slate/20 sm:p-3 dark:border-cyan-900/40">
              <code className="text-xs font-semibold">{`{{ ${variable.key} }}`}</code>
              <p className="mt-1 text-xs sm:text-sm">{variable.description}</p>
              <p className="mt-1 text-xs text-slate dark:text-white/60">Example: {variable.example}</p>
            </div>
          ))}
          {variablesQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading variables...</p>}
          {variablesQuery.isError && (
            <p className="text-sm text-red-600">
              {resolveApiErrorMessage(variablesQuery.error, 'Failed to load template variables.')}
            </p>
          )}
        </div>
      </div>
    </section>
  )
}
