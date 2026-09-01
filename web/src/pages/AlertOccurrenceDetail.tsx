import { useEffect, useRef } from 'react'

import type { AlertOccurrence, AlertOccurrenceActivity } from '../types/alerts'
import { formatDateTime } from '../utils/datetime'
import {
  ALERT_OCCURRENCE_ACTIVITY_PAGE_SIZE,
  alertOccurrencePageCount,
  formatActivityDetailKey,
  formatActivityDetailValue,
  formatAlertDisposition,
  formatAlertOccurrenceActivity,
  getAlertOccurrenceLifecycleActions,
  getAlertOccurrenceSource,
  shortIdentifier,
} from './alertOccurrenceModel'
import {
  AlertOccurrencePageError,
  AlertOccurrenceRefreshWarning,
  AlertOccurrenceStateChip,
  AlertOccurrenceStateFlags,
  AlertSeverityChip,
} from './AlertOccurrenceShared'
import type { AlertOccurrencesController } from './useAlertOccurrencesController'

export function AlertOccurrenceDetail({ controller }: { controller: AlertOccurrencesController }) {
  const panelRef = useRef<HTMLElement | null>(null)
  const focusedSelectionRef = useRef<string | null>(null)
  const { detailQuery, selectedOccurrenceId } = controller

  useEffect(() => {
    if (!selectedOccurrenceId) {
      focusedSelectionRef.current = null
      return
    }
    if (
      (detailQuery.data || detailQuery.isError) &&
      focusedSelectionRef.current !== selectedOccurrenceId
    ) {
      panelRef.current?.focus()
      focusedSelectionRef.current = selectedOccurrenceId
    }
  }, [detailQuery.data, detailQuery.isError, selectedOccurrenceId])

  if (!selectedOccurrenceId) {
    return (
      <aside className="border-t border-slate/15 px-3 py-5 dark:border-white/10 xl:border-l xl:border-t-0 xl:px-4">
        <h3 className="text-sm font-semibold">Occurrence details</h3>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          Select an occurrence to inspect source evidence, lifecycle actions, and activity history.
        </p>
      </aside>
    )
  }

  if (!detailQuery.data && detailQuery.isLoading) {
    return (
      <aside
        ref={panelRef}
        tabIndex={-1}
        aria-busy="true"
        className="border-t border-slate/15 px-3 py-5 outline-none dark:border-white/10 xl:border-l xl:border-t-0 xl:px-4"
      >
        <p role="status" className="text-sm text-slate dark:text-slate-300">
          Loading occurrence details...
        </p>
      </aside>
    )
  }

  if (!detailQuery.data && detailQuery.isError) {
    return (
      <aside
        ref={panelRef}
        tabIndex={-1}
        className="border-t border-slate/15 outline-none dark:border-white/10 xl:border-l xl:border-t-0"
      >
        <AlertOccurrencePageError
          error={detailQuery.error}
          fallback="The occurrence details could not be loaded"
          onRetry={() => void detailQuery.refetch()}
        />
        <button
          type="button"
          className="m-3 min-h-11 rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold sm:min-h-10 dark:border-white/15"
          onClick={controller.closeOccurrenceDetail}
        >
          Close details
        </button>
      </aside>
    )
  }

  if (!detailQuery.data) return null
  const occurrence = detailQuery.data
  const source = getAlertOccurrenceSource(occurrence)
  const actions = getAlertOccurrenceLifecycleActions(occurrence.lifecycle_state)
  const pendingLifecycleState = pendingLifecycleStateFor(controller, occurrence)
  const snoozePending = snoozePendingFor(controller, occurrence)

  return (
    <aside
      ref={panelRef}
      tabIndex={-1}
      aria-labelledby="alert-occurrence-detail-heading"
      className="min-w-0 border-t border-slate/15 outline-none dark:border-white/10 xl:border-l xl:border-t-0"
    >
      {detailQuery.isError && (
        <AlertOccurrenceRefreshWarning
          error={detailQuery.error}
          fallback="The occurrence details could not be refreshed"
          onRetry={() => void detailQuery.refetch()}
        />
      )}
      <header className="border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase text-slate dark:text-slate-400">
              Occurrence
            </p>
            <h3
              id="alert-occurrence-detail-heading"
              className="mt-1 break-words text-base font-semibold"
            >
              {source.title}
            </h3>
          </div>
          <button
            type="button"
            className="min-h-11 min-w-11 shrink-0 rounded border border-slate/25 px-2 py-1 text-xs font-semibold sm:min-h-0 sm:min-w-0 dark:border-white/15"
            aria-label="Close occurrence details"
            onClick={controller.closeOccurrenceDetail}
          >
            Close
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <AlertSeverityChip severity={occurrence.severity_snapshot} />
          <AlertOccurrenceStateChip state={occurrence.lifecycle_state} />
          <AlertOccurrenceStateFlags occurrence={occurrence} />
        </div>
        <p className="mt-2 text-xs text-slate dark:text-slate-400">
          {detailQuery.dataUpdatedAt > 0
            ? `Checked ${formatDateTime(new Date(detailQuery.dataUpdatedAt))} · `
            : ''}
          version {occurrence.version}
        </p>
      </header>

      {controller.writeDenied && (
        <p
          role="alert"
          className="border-b border-amber-300/50 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-200 sm:px-4"
        >
          This session can view occurrences but cannot update them. Verify it has permission to
          manage alerts; API tokens also need write:alerts.
        </p>
      )}

      <section
        className="border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4"
        aria-labelledby="occurrence-actions-heading"
      >
        <h4
          id="occurrence-actions-heading"
          className="text-xs font-semibold uppercase text-slate dark:text-slate-400"
        >
          Actions
        </h4>
        {occurrence.lifecycle_state === 'closed' && (
          <p className="mt-2 text-sm text-slate dark:text-slate-300">
            Closed occurrences cannot be reopened. Their closure disposition can still be corrected.
          </p>
        )}
        <div className="mt-2 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
          {actions.includes('acknowledge') && (
            <ActionButton
              label={acknowledgeActionLabel(pendingLifecycleState)}
              disabled={controller.mutationPending || controller.writeDenied}
              onClick={() => controller.runLifecycleAction(occurrence, 'acknowledge')}
            />
          )}
          {actions.includes('investigate') && (
            <ActionButton
              label={investigateActionLabel(pendingLifecycleState)}
              disabled={controller.mutationPending || controller.writeDenied}
              onClick={() => controller.runLifecycleAction(occurrence, 'investigate')}
            />
          )}
          {actions.includes('close') && (
            <ActionButton
              label="Close occurrence"
              primary
              disabled={controller.mutationPending || controller.writeDenied}
              onClick={() => controller.runLifecycleAction(occurrence, 'close')}
            />
          )}
          {actions.includes('change_disposition') && (
            <ActionButton
              label="Change closure disposition"
              disabled={controller.mutationPending || controller.writeDenied}
              onClick={() => controller.runLifecycleAction(occurrence, 'change_disposition')}
            />
          )}
          {occurrence.lifecycle_state !== 'closed' && !occurrence.is_snoozed && (
            <ActionButton
              label="Snooze"
              disabled={controller.mutationPending || controller.writeDenied}
              onClick={() => controller.requestSnooze(occurrence)}
            />
          )}
          {occurrence.lifecycle_state !== 'closed' && occurrence.snoozed_until && (
            <ActionButton
              label={clearSnoozeActionLabel(snoozePending, occurrence.is_snoozed)}
              disabled={controller.mutationPending || controller.writeDenied}
              onClick={() => controller.clearSnooze(occurrence)}
            />
          )}
        </div>
      </section>

      <section
        className="border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4"
        aria-labelledby="occurrence-evidence-heading"
      >
        <h4
          id="occurrence-evidence-heading"
          className="text-xs font-semibold uppercase text-slate dark:text-slate-400"
        >
          Evidence snapshot
        </h4>
        <dl className="mt-2 grid gap-x-3 gap-y-2 text-sm sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
          <DetailTerm
            label="Rule"
            value={`${occurrence.alert_name_snapshot} · revision ${occurrence.rule_revision}`}
          />
          <DetailTerm label="Category" value={occurrence.alert_category_snapshot} />
          <DetailTerm label="Feed" value={source.feedName ?? 'Unavailable'} />
          <DetailTerm label="Classification" value={source.classification ?? 'Unavailable'} />
          <DetailTerm label="First seen" value={formatDateTime(source.firstSeenAt)} />
          <DetailTerm label="Published" value={formatDateTime(source.publishedAt)} />
        </dl>
        {source.summary && (
          <p className="mt-3 whitespace-pre-wrap break-words text-sm text-slate dark:text-slate-300">
            {source.summary}
          </p>
        )}
        {source.url && (
          <a
            className="mt-3 inline-flex min-h-11 items-center rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold text-blue-700 sm:min-h-10 dark:border-white/15 dark:text-cyan"
            href={source.url}
            target="_blank"
            rel="noreferrer"
          >
            Open source article
          </a>
        )}
        <div className="mt-3">
          <p className="text-xs font-semibold text-slate dark:text-slate-400">Matched keywords</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {occurrence.matched_keywords.map((keyword) => (
              <span key={keyword} className="tl-chip tl-chip-neutral">
                {keyword}
              </span>
            ))}
          </div>
        </div>
      </section>

      <OccurrenceStateDetails occurrence={occurrence} />
      <OccurrenceActivity controller={controller} />

      <section
        className="px-3 py-3 text-xs text-slate dark:text-slate-400 sm:px-4"
        aria-labelledby="occurrence-identifiers-heading"
      >
        <h4 id="occurrence-identifiers-heading" className="font-semibold uppercase">
          Identifiers
        </h4>
        <dl className="mt-2 space-y-1.5">
          <DetailTerm label="Occurrence ID" value={occurrence.id} breakAll />
          <DetailTerm
            label="Item ID"
            value={occurrence.item_id ?? 'Source item removed'}
            breakAll
          />
          <DetailTerm
            label="Event ID"
            value={occurrence.integration_event_id ?? 'No notification event'}
            breakAll
          />
          <DetailTerm label="Content hash" value={occurrence.item_content_hash} breakAll />
        </dl>
      </section>
    </aside>
  )
}

function OccurrenceStateDetails({ occurrence }: { occurrence: AlertOccurrence }) {
  if (!occurrence.is_suppressed && !occurrence.snoozed_until && !occurrence.closure_disposition)
    return null
  return (
    <section
      className="border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4"
      aria-labelledby="occurrence-state-detail-heading"
    >
      <h4
        id="occurrence-state-detail-heading"
        className="text-xs font-semibold uppercase text-slate dark:text-slate-400"
      >
        State details
      </h4>
      <dl className="mt-2 space-y-2 text-sm">
        {occurrence.is_suppressed && (
          <DetailTerm
            label="Suppressed"
            value={`${formatDateTime(occurrence.suppressed_at)} · ${occurrence.suppression_reason ?? 'Reason unavailable'}`}
          />
        )}
        {occurrence.snoozed_until && (
          <DetailTerm
            label={occurrence.is_snoozed ? 'Snoozed until' : 'Snooze expired'}
            value={`${formatDateTime(occurrence.snoozed_until)} · ${occurrence.snooze_reason ?? 'Reason unavailable'}`}
          />
        )}
        {occurrence.closure_disposition && (
          <DetailTerm
            label="Closure disposition"
            value={formatAlertDisposition(occurrence.closure_disposition)}
          />
        )}
      </dl>
    </section>
  )
}

function OccurrenceActivity({ controller }: { controller: AlertOccurrencesController }) {
  const { activityQuery } = controller
  const data = activityQuery.data
  const pageCount = alertOccurrencePageCount(
    data?.total ?? 0,
    data?.page_size ?? ALERT_OCCURRENCE_ACTIVITY_PAGE_SIZE,
  )
  return (
    <section
      className="border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4"
      aria-labelledby="occurrence-activity-heading"
    >
      <div className="flex items-center justify-between gap-2">
        <h4
          id="occurrence-activity-heading"
          className="text-xs font-semibold uppercase text-slate dark:text-slate-400"
        >
          Activity {data ? `(${data.total})` : ''}
        </h4>
        <button
          type="button"
          className="min-h-11 min-w-11 rounded border border-slate/25 px-2 py-1 text-xs font-semibold disabled:opacity-60 sm:min-h-0 sm:min-w-0 dark:border-white/15"
          disabled={activityQuery.isFetching}
          aria-label="Refresh occurrence activity"
          onClick={() => void activityQuery.refetch()}
        >
          {activityQuery.isFetching ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>
      {!data && activityQuery.isLoading && (
        <p role="status" className="mt-3 text-sm text-slate dark:text-slate-300">
          Loading activity...
        </p>
      )}
      {!data && activityQuery.isError && (
        <div className="mt-3">
          <AlertOccurrencePageError
            error={activityQuery.error}
            fallback="Occurrence activity could not be loaded"
            onRetry={() => void activityQuery.refetch()}
          />
        </div>
      )}
      {data && activityQuery.isError && (
        <div className="mt-3">
          <AlertOccurrenceRefreshWarning
            error={activityQuery.error}
            fallback="Occurrence activity could not be refreshed"
            onRetry={() => void activityQuery.refetch()}
          />
        </div>
      )}
      {data && data.items.length === 0 && (
        <p className="mt-3 text-sm text-slate dark:text-slate-300">
          No activity has been recorded.
        </p>
      )}
      {data && data.items.length > 0 && (
        <ol className="mt-3 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
          {data.items.map((entry) => (
            <ActivityEntry key={entry.id} entry={entry} />
          ))}
        </ol>
      )}
      {data && pageCount > 1 && (
        <div className="mt-3 flex items-center justify-between gap-2 text-xs">
          <button
            type="button"
            className="min-h-11 rounded border border-slate/25 px-2.5 py-1 font-semibold disabled:opacity-50 sm:min-h-9 dark:border-white/15"
            disabled={controller.activityPage <= 1 || activityQuery.isFetching}
            aria-label="Previous occurrence activity page"
            onClick={() => controller.setActivityPage(controller.activityPage - 1)}
          >
            Previous
          </button>
          <span>
            Page {controller.activityPage} of {pageCount}
          </span>
          <button
            type="button"
            className="min-h-11 rounded border border-slate/25 px-2.5 py-1 font-semibold disabled:opacity-50 sm:min-h-9 dark:border-white/15"
            disabled={controller.activityPage >= pageCount || activityQuery.isFetching}
            aria-label="Next occurrence activity page"
            onClick={() => controller.setActivityPage(controller.activityPage + 1)}
          >
            Next
          </button>
        </div>
      )}
    </section>
  )
}

function ActivityEntry({ entry }: { entry: AlertOccurrenceActivity }) {
  const details = Object.entries(entry.details_json)
  return (
    <li className="py-2.5 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="font-semibold">{formatAlertOccurrenceActivity(entry.action)}</p>
        <time className="text-xs text-slate dark:text-slate-400" dateTime={entry.created_at}>
          {formatDateTime(entry.created_at)}
        </time>
      </div>
      <p className="mt-0.5 text-xs text-slate dark:text-slate-400">
        {entry.actor_user_id ? `Actor ${shortIdentifier(entry.actor_user_id)}` : 'System'}
      </p>
      {details.length > 0 && (
        <dl className="mt-1.5 grid gap-x-2 gap-y-1 text-xs sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
          {details.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="text-slate dark:text-slate-400">{formatActivityDetailKey(key)}</dt>
              <dd className="break-words text-ink dark:text-slate-200">
                {formatActivityDetailValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}

function DetailTerm({
  label,
  value,
  breakAll = false,
}: {
  label: string
  value: string
  breakAll?: boolean
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate dark:text-slate-400">{label}</dt>
      <dd
        className={`${breakAll ? 'break-all font-mono' : 'break-words'} text-ink dark:text-slate-200`}
      >
        {value}
      </dd>
    </div>
  )
}

function ActionButton({
  label,
  disabled,
  primary = false,
  onClick,
}: {
  label: string
  disabled: boolean
  primary?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={
        primary
          ? 'min-h-11 rounded bg-ink px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50 sm:min-h-10 dark:bg-cyan dark:text-[#053c2e]'
          : 'min-h-11 rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold disabled:opacity-50 sm:min-h-10 dark:border-white/15'
      }
      disabled={disabled}
      onClick={onClick}
    >
      {label}
    </button>
  )
}

function pendingLifecycleStateFor(
  controller: AlertOccurrencesController,
  occurrence: AlertOccurrence,
) {
  const input = controller.lifecycleMutation.variables
  if (!controller.lifecycleMutation.isPending || input?.occurrence.id !== occurrence.id) {
    return null
  }
  return input.state
}

function snoozePendingFor(
  controller: AlertOccurrencesController,
  occurrence: AlertOccurrence,
) {
  return (
    controller.snoozeMutation.isPending &&
    controller.snoozeMutation.variables?.occurrence.id === occurrence.id
  )
}

function acknowledgeActionLabel(pendingState: string | null) {
  return pendingState === 'acknowledged' ? 'Acknowledging...' : 'Acknowledge'
}

function investigateActionLabel(pendingState: string | null) {
  return pendingState === 'investigating' ? 'Starting investigation...' : 'Start investigating'
}

function clearSnoozeActionLabel(pending: boolean, snoozed: boolean) {
  if (pending) return 'Clearing snooze...'
  return snoozed ? 'Clear snooze' : 'Clear expired snooze'
}
