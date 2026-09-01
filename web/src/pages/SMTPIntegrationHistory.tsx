import { useState } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import { formatDateTime } from '../utils/datetime'
import {
  Feed,
  SMTPAnalyticsResponse,
  SMTPDelivery,
  SMTPDeliveryListResponse,
  SMTPHook,
  SMTPTestRunListResponse,
} from '../types/api'
import {
  deliveryStateBadgeClass,
  describeDeliveryState,
  describeEventType,
} from './smtpIntegrationPresentation'

export function SMTPAnalyticsPanel({
  analytics,
  loading,
  error,
}: {
  analytics?: SMTPAnalyticsResponse
  loading: boolean
  error: unknown
}) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg">Email delivery health</h2>
          <p className="mt-1 text-sm text-slate dark:text-white/75">Delivery health across all active and retained email destinations.</p>
        </div>
        {loading && <span className="text-sm text-slate dark:text-white/70">Loading analytics...</span>}
      </div>
      {Boolean(error) && (
        <p className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(error, 'Failed to load email delivery health.')}
        </p>
      )}
      {analytics && <SMTPAnalyticsDetails analytics={analytics} />}
    </section>
  )
}

function SMTPAnalyticsDetails({ analytics }: { analytics: SMTPAnalyticsResponse }) {
  return (
    <div className="mt-3 space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:gap-3 xl:grid-cols-5">
        <Metric label="Enabled destinations" value={`${analytics.enabled_hook_count} / ${analytics.hook_count}`} />
        <Metric label="Total deliveries" value={String(analytics.total_deliveries)} />
        <Metric label="Success rate" value={`${analytics.success_rate_pct.toFixed(1)}%`} />
        <Metric label="Failures 24h" value={String(analytics.failures_last_24h)} />
        <Metric label="Queued / retry" value={`${analytics.pending_deliveries} / ${analytics.retry_wait_deliveries}`} />
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="rounded-lg border border-slate/20 px-3 py-3 dark:border-cyan-900/40">
          <p className="text-sm font-semibold">Event breakdown</p>
          {analytics.events.length ? (
            <div className="mt-2 grid gap-x-5 gap-y-2 sm:grid-cols-2">
              {analytics.events.map((event) => (
                <div key={event.event_type} className="flex items-center justify-between gap-3 text-sm">
                  <span>{describeEventType(event.event_type)}</span>
                  <span className="font-semibold">{event.failed_deliveries} / {event.total_deliveries} failed</span>
                </div>
              ))}
            </div>
          ) : <p className="mt-2 text-sm text-slate dark:text-white/70">No email deliveries recorded yet.</p>}
        </div>
        <div className="rounded-lg border border-slate/20 px-3 py-3 dark:border-cyan-900/40">
          <p className="text-sm font-semibold">Most failing destination</p>
          <p className="mt-2 truncate text-sm font-semibold">{analytics.most_failing_hook?.hook_name ?? 'None'}</p>
          <p className="mt-1 text-xs text-slate dark:text-white/60">
            {analytics.most_failing_hook
              ? `${analytics.most_failing_hook.failed_deliveries} retained failures`
              : 'No terminal failures recorded.'}
          </p>
        </div>
      </div>
    </div>
  )
}

type SMTPDeliveryHistoryProps = {
  hook: SMTPHook | null
  feeds: Feed[]
  deliveries?: SMTPDeliveryListResponse
  deliveryLoading: boolean
  deliveryError: unknown
  deliveryPage: number
  onDeliveryPageChange: (page: number) => void
  testRuns?: SMTPTestRunListResponse
  testRunLoading: boolean
  testRunError: unknown
  testRunPage: number
  onTestRunPageChange: (page: number) => void
  replaying: boolean
  canReplay: boolean
  onReplay: (delivery: SMTPDelivery) => void
}

export function SMTPDeliveryHistory(props: SMTPDeliveryHistoryProps) {
  const [historyView, setHistoryView] = useState<'deliveries' | 'tests'>('deliveries')
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg">Email delivery history</h2>
          <p className="mt-1 text-sm text-slate dark:text-white/75">Inspect deliveries, test diagnostics, retry attempts, and transport responses for this destination.</p>
        </div>
        <HistoryTabs historyView={historyView} onChange={setHistoryView} />
      </div>
      {historyView === 'deliveries'
        ? <SMTPDeliveriesPanel {...props} />
        : <SMTPTestRunsPanel {...props} />}
    </section>
  )
}

function HistoryTabs({
  historyView,
  onChange,
}: {
  historyView: 'deliveries' | 'tests'
  onChange: (view: 'deliveries' | 'tests') => void
}) {
  const inactiveClass = 'text-slate dark:text-white/75'
  const activeClass = 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
  return (
    <div role="tablist" aria-label="Email delivery history view" className="flex rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40">
      <button
        type="button"
        role="tab"
        id="smtp-deliveries-tab"
        aria-selected={historyView === 'deliveries'}
        aria-controls="smtp-deliveries-panel"
        className={`rounded px-3 py-1 text-sm ${historyView === 'deliveries' ? activeClass : inactiveClass}`}
        onClick={() => onChange('deliveries')}
      >
        Deliveries
      </button>
      <button
        type="button"
        role="tab"
        id="smtp-tests-tab"
        aria-selected={historyView === 'tests'}
        aria-controls="smtp-tests-panel"
        className={`rounded px-3 py-1 text-sm ${historyView === 'tests' ? activeClass : inactiveClass}`}
        onClick={() => onChange('tests')}
      >
        Tests
      </button>
    </div>
  )
}

function SMTPDeliveriesPanel({
  hook,
  feeds,
  deliveries,
  deliveryLoading,
  deliveryError,
  deliveryPage,
  onDeliveryPageChange,
  replaying,
  canReplay,
  onReplay,
}: SMTPDeliveryHistoryProps) {
  if (!hook) {
    return <HistoryMessage panel="deliveries" message="Select an email destination to view delivery history." />
  }
  if (deliveryLoading) {
    return <HistoryMessage panel="deliveries" message="Loading delivery history..." />
  }
  if (deliveryError) {
    return <HistoryError panel="deliveries" error={deliveryError} fallback="Failed to load email delivery history." />
  }
  if (!deliveries?.deliveries.length) {
    return <HistoryMessage panel="deliveries" message="No deliveries have been recorded for this destination." />
  }
  const latest = deliveries.deliveries[0]
  const metricPrefix = deliveryPage === 1 ? 'Latest' : 'First shown'
  return (
    <div id="smtp-deliveries-panel" role="tabpanel" aria-labelledby="smtp-deliveries-tab" className="mt-3 space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Deliveries" value={String(deliveries.total)} />
        <Metric label={`${metricPrefix} attempts`} value={String(latest.attempt_count)} />
        <Metric label={`${metricPrefix} duration`} value={latest.last_duration_ms != null ? `${latest.last_duration_ms} ms` : 'n/a'} />
        <Metric label={`${metricPrefix} updated`} value={formatDateTime(latest.updated_at)} />
      </div>
      {deliveries.deliveries.map((delivery) => (
        <SMTPDeliveryDetails
          key={delivery.id}
          delivery={delivery}
          feedName={feeds.find((feed) => feed.id === delivery.feed_id)?.name}
          replaying={replaying}
          canReplay={canReplay}
          onReplay={onReplay}
        />
      ))}
      <HistoryPagination page={deliveryPage} pageSize={deliveries.page_size} total={deliveries.total} onPageChange={onDeliveryPageChange} />
    </div>
  )
}

function SMTPDeliveryDetails({
  delivery,
  feedName,
  replaying,
  canReplay,
  onReplay,
}: {
  delivery: SMTPDelivery
  feedName?: string
  replaying: boolean
  canReplay: boolean
  onReplay: (delivery: SMTPDelivery) => void
}) {
  const title = feedName || (delivery.feed_id ? `Feed ${delivery.feed_id.slice(0, 8)}` : 'Email delivery')
  return (
    <details className="rounded-lg border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
      <summary className="cursor-pointer list-none">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`tl-chip ${deliveryStateBadgeClass(delivery.state)}`}>{describeDeliveryState(delivery.state)}</span>
              <span className="tl-chip tl-chip-neutral">{delivery.delivery_kind === 'replay' ? 'Replay' : 'Live'}</span>
              <span className="tl-chip tl-chip-neutral">{describeEventType(delivery.event_type)}</span>
            </div>
            <p className="mt-2 text-sm font-semibold">{title}</p>
            <p className="mt-1 text-xs text-slate dark:text-white/60">Created {formatDateTime(delivery.created_at)}</p>
          </div>
          <div className="text-right text-xs text-slate dark:text-white/60">
            <p>{delivery.last_duration_ms != null ? `${delivery.last_duration_ms} ms` : 'No duration'}</p>
            <p>{delivery.attempt_count} of {delivery.max_attempts} attempts</p>
          </div>
        </div>
      </summary>
      <div className="mt-3 space-y-3 text-sm">
        {delivery.state === 'dead_letter' && (
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 dark:border-cyan-900/40"
            disabled={replaying || !canReplay}
            onClick={() => {
              if (canReplay) onReplay(delivery)
            }}
          >
            Replay dead-letter delivery
          </button>
        )}
        {delivery.last_error_message && (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-red-700 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200">
            <p className="font-semibold">{delivery.last_error_code || 'Delivery error'}</p>
            <p className="mt-1 break-words text-xs">{delivery.last_error_message}</p>
          </div>
        )}
        <DeliveryAttempts delivery={delivery} />
      </div>
    </details>
  )
}

function DeliveryAttempts({ delivery }: { delivery: SMTPDelivery }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">Attempts</p>
      {delivery.attempts.length ? (
        <div className="mt-2 space-y-2">
          {delivery.attempts.map((attempt) => (
            <div key={attempt.attempt_number} className="grid gap-1 rounded bg-slate/5 px-3 py-2 text-xs dark:bg-white/5 sm:grid-cols-[80px_1fr_auto]">
              <span className="font-semibold">Attempt {attempt.attempt_number}</span>
              <span>{attempt.error_message || `${attempt.accepted_count ?? 0} of ${attempt.recipient_count ?? 0} recipients accepted`}</span>
              <span>{attempt.duration_ms != null ? `${attempt.duration_ms} ms` : attempt.status}</span>
            </div>
          ))}
        </div>
      ) : <p className="mt-2 text-xs text-slate dark:text-white/60">No worker attempt has started yet.</p>}
    </div>
  )
}

function SMTPTestRunsPanel({
  hook,
  testRuns,
  testRunLoading,
  testRunError,
  testRunPage,
  onTestRunPageChange,
}: SMTPDeliveryHistoryProps) {
  if (!hook) {
    return <HistoryMessage panel="tests" message="Save this email destination before its tests can be retained in history." />
  }
  if (testRunLoading) {
    return <HistoryMessage panel="tests" message="Loading email test history..." />
  }
  if (testRunError) {
    return <HistoryError panel="tests" error={testRunError} fallback="Failed to load email test history." />
  }
  if (!testRuns?.runs.length) {
    return <HistoryMessage panel="tests" message="No email tests have been recorded for this destination yet." />
  }
  const latest = testRuns.runs[0]
  const metricPrefix = testRunPage === 1 ? 'Latest' : 'First shown'
  return (
    <div id="smtp-tests-panel" role="tabpanel" aria-labelledby="smtp-tests-tab" className="mt-3 space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Tests" value={String(testRuns.total)} />
        <Metric label={`${metricPrefix} result`} value={latest.status === 'succeeded' ? 'Succeeded' : 'Failed'} />
        <Metric label={`${metricPrefix} duration`} value={latest.duration_ms != null ? `${latest.duration_ms} ms` : 'n/a'} />
        <Metric label={`${metricPrefix} tested`} value={formatDateTime(latest.started_at)} />
      </div>
      {testRuns.runs.map((run) => (
        <details key={run.id} open={run.status === 'failed'} className="rounded-lg border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
          <summary className="cursor-pointer list-none">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`tl-chip ${run.status === 'succeeded' ? 'tl-chip-success' : 'tl-chip-danger'}`}>{run.status === 'succeeded' ? 'Succeeded' : 'Failed'}</span>
                  <span className="tl-chip tl-chip-neutral">{run.action === 'send' ? 'Test email' : run.action === 'connection' ? 'Connection test' : 'Legacy test'}</span>
                  <span className="tl-chip tl-chip-neutral">{run.used_unsaved_settings ? 'Draft settings' : 'Saved settings'}</span>
                </div>
                <p className="mt-2 text-sm font-semibold">{run.recipient_email ? `Recipient: ${run.recipient_email}` : 'Connection and authentication only'}</p>
                <p className="mt-1 text-xs text-slate dark:text-white/60">Started {formatDateTime(run.started_at)}</p>
              </div>
              <div className="text-right text-xs text-slate dark:text-white/60">
                <p>{run.duration_ms != null ? `${run.duration_ms} ms` : 'No duration'}</p>
                <p>{run.finished_at ? `Finished ${formatDateTime(run.finished_at)}` : 'Finish time unavailable'}</p>
              </div>
            </div>
          </summary>
          <div className="mt-3 space-y-3 text-sm">
            {(run.error_code || run.error_message) && (
              <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-red-700 dark:border-red-900/50 dark:bg-red-950/35 dark:text-red-200">
                <p className="font-semibold">{run.error_code || 'SMTP test error'}</p>
                {run.error_message && <p className="mt-1 break-words text-xs">{run.error_message}</p>}
              </div>
            )}
            <div>
              <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">SMTP server response</p>
              {run.server_message
                ? <code className="mt-2 block whitespace-pre-wrap break-words rounded bg-slate/10 px-3 py-2 text-xs dark:bg-white/5">{run.server_message}</code>
                : <p className="mt-2 text-xs text-slate dark:text-white/60">No SMTP server response was captured for this test.</p>}
            </div>
            <p className="break-all text-xs text-slate dark:text-white/50">Run ID: {run.id}</p>
          </div>
        </details>
      ))}
      <HistoryPagination page={testRunPage} pageSize={testRuns.page_size} total={testRuns.total} onPageChange={onTestRunPageChange} />
    </div>
  )
}

function HistoryMessage({ panel, message }: { panel: 'deliveries' | 'tests'; message: string }) {
  return (
    <div id={`smtp-${panel}-panel`} role="tabpanel" aria-labelledby={`smtp-${panel}-tab`}>
      <p className="mt-3 text-sm text-slate dark:text-white/70">{message}</p>
    </div>
  )
}

function HistoryError({ panel, error, fallback }: { panel: 'deliveries' | 'tests'; error: unknown; fallback: string }) {
  return (
    <div id={`smtp-${panel}-panel`} role="tabpanel" aria-labelledby={`smtp-${panel}-tab`}>
      <p className="mt-3 text-sm text-red-600">{resolveApiErrorMessage(error, fallback)}</p>
    </div>
  )
}

function HistoryPagination({
  page,
  pageSize,
  total,
  onPageChange,
}: {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
}) {
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)))
  if (totalPages === 1) return null
  return (
    <div className="flex items-center justify-between gap-3 border-t border-slate/20 pt-3 text-sm dark:border-cyan-900/40">
      <button type="button" className="rounded border border-slate/30 px-3 py-1.5 font-semibold disabled:opacity-40 dark:border-cyan-900/40" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>Previous</button>
      <span className="text-slate dark:text-white/65">Page {page} of {totalPages}</span>
      <button type="button" className="rounded border border-slate/30 px-3 py-1.5 font-semibold disabled:opacity-40 dark:border-cyan-900/40" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>Next</button>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-l border-slate/20 py-1 pl-3 dark:border-cyan-900/40">
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold">{value}</p>
    </div>
  )
}
